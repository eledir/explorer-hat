print('main being executed')
print(__name__)

import sys
import time
import threading
import signal
import atexit
import captouch
import RPi.GPIO as GPIO
from pins import ObjectCollection, AsyncWorker, StoppableThread

explorer_pro = False

# Assume A+, B+ and no funny business

# Onboard LEDs above 1, 2, 3, 4
LED1 = 4
LED2 = 17
LED3 = 27
LED4 = 5

# Outputs via ULN2003A
OUT1 = 6
OUT2 = 12
OUT3 = 13
OUT4 = 16

# 5v Tolerant Inputs
IN1  = 23
IN2  = 22
IN3  = 24
IN4  = 25

# Motor, via DRV8833PWP Dual H-Bridge
M1B  = 19
M1F  = 20
M2B  = 21
M2F  = 26

# Number of times to udpate
# pulsing LEDs per second
PULSE_FPS = 50
PULSE_FREQUENCY = 100

DEBOUNCE_TIME = 20

CAP_PRODUCT_ID = 107

class Pulse(StoppableThread):
    '''Thread wrapper class for delta-timed LED pulsing

    Pulses an LED to wall-clock time.
    '''
    def __init__(self,pin,time_on,time_off,transition_on,transition_off):
        StoppableThread.__init__(self)

        self.pin = pin
        self.time_on = (time_on)
        self.time_off = (time_off)
        self.transition_on = (transition_on)
        self.transition_off = (transition_off)

        self.fps = PULSE_FPS

        # Total time of transition
        self.time_start = time.time()

    def start(self):
        self.time_start = time.time()
        StoppableThread.start(self)

    def run(self):
        while self.stop_event.is_set() == False:
            current_time = time.time() - self.time_start
            delta = current_time % (self.transition_on+self.time_on+self.transition_off+self.time_off)

            if( delta <= self.transition_on ):
                # Transition On Phase
                self.pin.duty_cycle( round(( 100.0 / self.transition_on ) * delta) )

            elif( delta > self.transition_on + self.time_on and delta <= self.transition_on + self.time_on + self.transition_off ):
                # Transition Off Phase
                current_delta = delta - self.transition_on - self.time_on
                self.pin.duty_cycle( round(100.0 - ( ( 100.0 / self.transition_off ) * current_delta )) )

            elif( delta > self.transition_on and delta <= self.transition_on + self.time_on ):
                self.pin.duty_cycle( 100 )

            elif( delta > self.transition_on + self.time_on + self.transition_off ):
                self.pin.duty_cycle( 0 )

            time.sleep(1.0/self.fps)

        self.pin.duty_cycle( 0 )

class Pin(object):
    '''Base class representing a GPIO Pin
     
    Pin contains methods that apply to both inputs and outputs
    '''
    type = 'Pin'

    def __init__(self, pin):
        self.pin = pin
        self.last = self.read()
        self.handle_change = False
        self.handle_high = False
        self.handle_low = False

    # Return a tidy list of  all "public" methods
    def __call__(self):
        return filter(lambda x: x[0] != '_', dir(self))

    def has_changed(self):
        if self.read() != self.last:
            self.last = self.read()
            return True
        return False

    def is_off(self):
        '''Returns True if pin is in LOW/OFF state'''
        return self.read() == 0

    def is_on(self):
        '''Returns True if pin is in HIGH/ON state'''
        return self.read() == 1

    def read(self):
        '''Returns HIGH or LOW value of pin'''
        return GPIO.input(self.pin)

    def stop(self):
        return True

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        pass

    is_high = is_on
    is_low = is_off
    get = read

class Motor(object):
    '''Class representing a motor driver channel

    Contains methods for driving the motor at variable speeds
    '''
    type = 'Motor'
    
    def __init__(self, pin_fw, pin_bw):
        self.pwm = None
        self.pwm_pin = None
        self._invert = False
        self.pin_fw = pin_fw
        self.pin_bw = pin_bw
        self._speed = 0
        GPIO.setup(self.pin_fw, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.pin_bw, GPIO.OUT, initial=GPIO.LOW)

    def invert(self):
        '''Inverts the motors direction'''
        self._invert = not self._invert
        self._speed = -self._speed
        self.speed(self._speed)
        return self._invert

    def forwards(self, speed=100):
        '''Drives the motor forwards at given speed

        Arguments:
        * speed - Value from 0 to 100
        '''
        if speed > 100 or speed < 0:
            raise ValueError("Speed must be between 0 and 100")
            return False
        if self._invert:
            self.speed(-speed)
        else:
            self.speed(speed)

    def backwards(self, speed=100):
        '''Drives the motor backwards at given speed

        Arguments:
        * speed - Value from 0 to 100
        '''
        if speed > 100 or speed < 0:
            raise ValueError("Speed must be between 0 and 100")
            return False 
        if self._invert:
            self.speed(speed)
        else:
            self.speed(-speed)

    def _duty_cycle(self, duty_cycle):
        if self.pwm != None:
            self.pwm.ChangeDutyCycle(duty_cycle)

    def _setup_pwm(self, pin, duty_cycle):
        if self.pwm_pin != pin:
            if self.pwm != None:
                self.pwm.stop()
                time.sleep(0.005)
            self.pwm = GPIO.PWM(pin, 100)
            self.pwm.start(duty_cycle)
            self.pwm_pin = pin

    def speed(self, speed=100):
        '''Drives the motor at a certain speed

        Arguments:
        * speed - Value from -100 to 100. 0 is stopped..
        '''
        if speed > 100 or speed < -100:
            raise ValueError("Speed must be between -100 and 100")
            return False
        self._speed = speed
        if speed > 0:
            GPIO.output(self.pin_bw, GPIO.LOW)
            self._setup_pwm(self.pin_fw, speed)
            self._duty_cycle(speed)
        if speed < 0:
            GPIO.output(self.pin_fw, GPIO.LOW)
            self._setup_pwm(self.pin_bw, abs(speed))
            self._duty_cycle(abs(speed))
        if speed == 0:
            if self.pwm != None:
              self.pwm.stop()
              time.sleep(0.005)
            self.pwm_pin = None
            self.pwm = None
        return speed

    def stop(self):
        '''Set the speed to 0'''
        self.speed(0)

    forward = forwards
    backward = backwards
    reverse = invert

class Input(Pin):
    '''Class representing a GPIO input

     Input only contains methods that apply to inputs
    '''

    type = 'Input'

    def __init__(self, pin):
        self.handle_pressed = None
        self.handle_released = None
        self.handle_changed = None
        self.has_callback = False
        if self.type == 'Button':
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        else:
            GPIO.setup(pin, GPIO.IN)
        super(Input,self).__init__(pin)

    def on_high(self, callback, bouncetime=DEBOUNCE_TIME):
        '''Attach a callback to trigger on a transition to HIGH'''
        self.handle_pressed = callback
        self._setup_callback(bouncetime)
        return True

    def _setup_callback(self, bouncetime):
        if self.has_callback:
            return False

        def handle_callback(pin):
            if self.read() == 1 and callable(self.handle_pressed):
                self.handle_pressed(self)
            elif self.read() == 0 and callable(self.handle_released):
                self.handle_released(self)
            if callable(self.handle_changed):
                self.handle_changed(self)
        GPIO.add_event_detect(self.pin, GPIO.BOTH, callback=handle_callback, bouncetime=bouncetime)
        self.has_callback = True
        return True

    def on_low(self, callback, bouncetime=DEBOUNCE_TIME):
        '''Attach a callback to trigger on transition to LOW'''
        self.handle_released = callback
        self._setup_callback(bouncetime)
        return True
        
    def on_changed(self, callback, bouncetime=DEBOUNCE_TIME):
        '''Attach a callback to trigger when changed'''
        self.handle_changed = callback
        self._setup_callback(bouncetime)
        return True

    def clear_events(self):
        '''Clear all attached callbacks'''
        self.handle_pressed = None
        self.handle_released = None
        self.handle_changed = None
        GPIO.remove_event_detect(self.pin)
        self.has_callback = False

    # Alias handlers
    changed = on_changed
    pressed = on_high
    released = on_low

class Output(Pin):
    '''Class representing a GPIO Output

    ONly contains methods that apply to outputs, including those for puling
    LEDs or other attached devices.
    '''
    type = 'Output'

    def __init__(self, pin):
        GPIO.setup(pin, GPIO.OUT, initial=0)
        super(Output,self).__init__(pin)
        self.gpio_pwm = GPIO.PWM(pin,1)

        self.pulser = Pulse(self,0,0,0,0)
        self.blinking = False
        self.pulsing = False
        self.fader = None

    def fade(self,start,end,duration):
        '''Fades an LED to a specific brightness over time

        Arguments:
        * start - Starting brightness, 0 to 255
        * end - Ending brightness, 0 to 255
        * duration - Duration in seconds
        '''
        self.stop()
        time_start = time.time()
        self.pwm(PULSE_FREQUENCY,start)
        def _fade():
            if time.time() - time_start >= duration:
                self.duty_cycle(end)
                return False
            
            current = (time.time() - time_start) / duration
            brightness = start + (float(end-start) * current)
            self.duty_cycle(round(brightness))
            time.sleep(0.1)
            
        self.fader = AsyncWorker(_fade)
        self.fader.start()
        return True

    def blink(self,on=1,off=-1):
        '''Blinks an LED by working out the correct PWM freq/duty

        Arguments:
        * on - On duration in seconds
        * off - Off duration in seconds
        '''
        if off == -1:
            off = on

        off = float(off)
        on = float(on)

        total = off + on

        duty_cycle = 100.0 * (on/total)

        # Stop the thread that's pulsing the LED
        if self.pulsing:
            self.stop_pulse();

        # Use pure PWM blinking, because threads are fugly
        if self.blinking:
            self.frequency(1.0/total)
            self.duty_cycle(duty_cycle)
        else:
            self.pwm(1.0/total,duty_cycle)
            self.blinking = True

        return True
    
    def pulse(self,transition_on=None,transition_off=None,time_on=None,time_off=None):
        '''Pulses an LED

        Arguments:
        * transition_on - Time in seconds that the transition from 0 to 100% brightness should take.
        * transition_off - Time in seconds that the transition from 100% to 0% brightness should take.
        * time_on - Time the LED should stay at 100% brightness
        * time_off - Time the LED should stay at 0% brightness
        '''
        # This needs a thread to handle the fade in and out

        # Attempt to cascade parameters
        # pulse() = pulse(0.5,0.5,0.5,0.5)
        # pulse(0.5,1.0) = pulse(0.5,1.0,0.5,0.5)
        # pulse(0.5,1.0,1.0) = pulse(0.5,1.0,1.0,1.0)
        # pulse(0.5,1.0,1.0,0.5) = -

        if transition_on == None:
            transition_on = 0.5
        if transition_off == None:
            transition_off = transition_on
        if time_on == None:
            time_on = transition_on
        if time_off == None:
            time_off = transition_on

        if self.blinking == False:
            self.pwm(PULSE_FREQUENCY,0.0)

        # pulse(x,y,0,0) is basically just a regular blink
        # only fire up a thread if we really need it
        if transition_on == 0 and transition_off == 0:
            self.blink(time_on,time_off)
        else:
            self.pulser.time_on = time_on
            self.pulser.time_off = time_off
            self.pulser.transition_on = transition_on
            self.pulser.transition_off = transition_off
            self.pulser.start()
            self.pulsing = True

        self.blinking = True

        return True

    def pwm(self,freq,duty_cycle = 50):
        '''Sets specified PWM Freq/Duty on a pin

        Arguments:
        * freq - Frequency in hz
        * duty_cycle - Value from 0 to 100
        '''
        self.gpio_pwm.ChangeDutyCycle(duty_cycle)
        self.gpio_pwm.ChangeFrequency(freq)
        self.gpio_pwm.start(duty_cycle)
        return True

    def frequency(self,freq):
        '''Change the PWM frequency'''
        self.gpio_pwm.ChangeFrequency(freq)
        return True

    def duty_cycle(self,duty_cycle):
        '''Change the PWM duty cycle'''
        self.gpio_pwm.ChangeDutyCycle(duty_cycle)
        return True

    def stop(self):
        '''Stop any running pulsing/blinking'''
        if self.fader != None:
            self.fader.stop()

        self.blinking = False
        self.stop_pulse()

        # Abruptly stopping PWM is a bad idea
        # unless we're writing a 1 or 0
        # So don't inherit the parent classes
        # stop() since weird bugs happen

        # Threaded PWM access was aborting with
        # no errors when stop coincided with a
        # duty cycle change.
        return True

    def stop_pulse(self):
        self.pulsing = False
        self.pulser.stop()
        self.pulser = Pulse(self,0,0,0,0)

    def write(self,value):
        '''Write a specific value to the output
          
        Arguments:
        * value - Should be 0 or 1 for LOW/HIGH respectively
        '''
        blinking = self.blinking

        self.stop()

        self.duty_cycle(100)
        self.gpio_pwm.stop()

        # Some gymnastics here to fix a bug ( in RPi.GPIO?)
        # That occurs when trying to output(1) immediately
        # after stopping the PWM

        # A small delay is needed. Ugly, but it works
        if blinking and value == 1:
            time.sleep(0.02)

        GPIO.output(self.pin,value)

        return True

    def on(self):
        '''Writes the value 1/HIGH/ON to the Output'''
        self.write(1)
        return True
    
    def off(self):
        '''Writes the value 0/LOW/OFF to the Output'''
        self.write(0)
        return True

    high = on
    low  = off

    def toggle(self):
        if( self.blinking ):
            self.write(0)
            return True

        if( self.read() == 1 ):
            self.write(0)
        else:
            self.write(1)
        return True

class Light(Output):
    '''Class representing an LED

    Contains methods that only apply to LEDs
    '''

    type = 'Light'

    def __init__(self,pin):
        super(Light,self).__init__(pin)


class AnalogInput(object):
    type = 'Analog Input'

    def __init__(self, channel):
        self.channel = channel
        self._sensitivity = 0.1
        self._t_watch = None
        self.last_value = None

    def read(self):
        return _analog.read_se_adc(self.channel)

    def sensitivity(self, sensitivity):
        self._sensitivity = sensitivity

    def changed(self, handler, sensitivity=None):
        self._handler = handler
        if sensitivity != None:
            self._sensitivity = sensitivity
        if self._t_watch == None:
            self._t_watch = AsyncWorker(self._watch)
            self._t_watch.start()
 
    def _watch(self):
        value = self.read()
        if self.last_value != None and abs(value-self.last_value) > self._sensitivity:
            if callable(self._handler):
                self._handler(self, value)
        self.last_value = value
        time.sleep(0.01)

class CapTouchSettings(object):
    type = 'Cap Touch Settings'

    def enable_multitouch(self, en=True):
        _cap1208.enable_multitouch(en)

class CapTouchInput(object):
    type = 'Cap Touch Input'
    
    def __init__(self, channel, alias):
        self.alias = alias
        self._pressed = False
        self._held = False
        self.channel = channel
        self.handlers = {'press':None, 'release':None, 'held':None}
        for event in ['press','release','held']:
            _cap1208.on(channel = self.channel, event=event, handler=self._handle_state)

    def _handle_state(self, channel,event):
        if channel == self.channel:
            if event == 'press':
                self._pressed = True
            elif event == 'held':
                self._held = True
            elif event in ['release','none']:
                self._pressed = False
                self._held = False
            if callable(self.handlers[event]):
                self.handlers[event](self.alias, event)

    def is_pressed(self):
        return self._pressed

    def is_held(self):
        return self._held

    def pressed(self, handler):
        self.handlers['press'] = handler

    def released(self, handler):
        self.handlers['release'] = handler

    def held(self, handler):
        self.handlers['held'] = handler

running = False
workers = {}

def async_start(name,function):
    global workers
    workers[name] = AsyncWorker(function)
    workers[name].start()
    return True

def async_stop(name):
    global workers
    workers[name].stop()
    return True

def async_stop_all():
    global workers
    for worker in workers:
        print("Stopping user task: " + worker)
        workers[worker].stop()
    return True

def set_timeout(function,seconds):
    def fn_timeout():
        time.sleep(seconds)
        function()
        return False
    timeout = AsyncWorker(fn_timeout)
    timeout.start()
    return True

def pause():
    signal.pause()

def loop(callback):
    global running
    running = True
    while running:
        callback()

def stop():
    global running
    running = False
    return True

def is_explorer_pro():
    return explorer_pro

def explorerhat_exit():
    print("\nExplorer HAT exiting cleanly, please wait...")

    print("Stopping flashy things...")
    try:
        output.stop()
        input.stop()
        light.stop()
    except AttributeError:
        pass

    print("Stopping user tasks...")
    async_stop_all()

    print("Cleaning up...")
    GPIO.cleanup()

    print("Goodbye!")

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)


_cap1208 = captouch.Cap1208()
if not _cap1208._get_product_id() == CAP_PRODUCT_ID:
    exit("Explorer HAT not found...\nHave you enabled i2c?")

_cap1208._write_byte(captouch.R_SAMPLING_CONFIG, 0b00001000)
_cap1208._write_byte(captouch.R_SENSITIVITY,     0b01100000)
_cap1208._write_byte(captouch.R_GENERAL_CONFIG,  0b00111000)
_cap1208._write_byte(captouch.R_CONFIGURATION2,  0b01100000)
_cap1208.set_touch_delta(10)
    
import analog as _analog
if _analog.adc_available:
    print("Explorer HAT Pro detected...")
    explorer_pro = True
else:    
    print("Explorer HAT Basic detected...")
    print("If this is incorrect, please check your i2c settings!")
    explorer_pro = False

atexit.register(explorerhat_exit)

try:
    settings = ObjectCollection()
    settings._add(touch = CapTouchSettings())
 
    light = ObjectCollection()
    light._add(blue   = Light(LED1))
    light._add(yellow = Light(LED2))
    light._add(red    = Light(LED3))
    light._add(green  = Light(LED4))
    light._alias(amber = 'yellow')

    output = ObjectCollection()
    output._add(one   = Output(OUT1))
    output._add(two   = Output(OUT2))
    output._add(three = Output(OUT3))
    output._add(four  = Output(OUT4))

    input = ObjectCollection()
    input._add(one   = Input(IN1))
    input._add(two   = Input(IN2))
    input._add(three = Input(IN3))
    input._add(four  = Input(IN4))


    touch = ObjectCollection()
    touch._add(one   = CapTouchInput(4,1))
    touch._add(two   = CapTouchInput(5,2))
    touch._add(three = CapTouchInput(6,3))
    touch._add(four  = CapTouchInput(7,4))
    touch._add(five  = CapTouchInput(0,5))
    touch._add(six   = CapTouchInput(1,6))
    touch._add(seven = CapTouchInput(2,7))
    touch._add(eight = CapTouchInput(3,8))

# Check for the existence of the ADC
# to determine if we're running Pro

    analog = ObjectCollection()
    motor  = ObjectCollection()
    if is_explorer_pro():
        motor._add(one = Motor(M1F, M1B))
        motor._add(two = Motor(M2F, M2B))
        analog._add(one   = AnalogInput(3))
        analog._add(two   = AnalogInput(2))
        analog._add(three = AnalogInput(1))
        analog._add(four  = AnalogInput(0))
except RuntimeError:
    print("You must be root to use Explorer HAT!")
    ready = False
