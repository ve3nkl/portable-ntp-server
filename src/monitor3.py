#!/usr/bin/python3
# -*- coding:utf-8 -*-

"""
-----------------------------------------------------------------------------------
Copyright (c) 2020, 2023 Nikolai Ozerov (VE3NKL)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

-----------------------------------------------------------------------------------
  Monitor configuration and status of GPSD and Chrony setup on Raspberry Pi Zero W.
-----------------------------------------------------------------------------------
"""

import sys
import math
import gpsdshm

import datetime as dt
import pytz
from timezonefinder import TimezoneFinder
import pyIGRF

import RPi.GPIO as GPIO
from threading import Timer, Lock, Event
from time import time, sleep

import subprocess as sp
from decimal import Decimal

sys.path.append("./RaspberryPi/python3")
import epd2in13
from PIL import Image,ImageDraw,ImageFont
import traceback

"""

  RepeatedTimer class helps to schedule or stop repeated calls to 
  a function. It is used to check the pressed buttons and to schedule
  regular screen updates.

"""

class RepeatedTimer:
  
  """
    Create a new Repeated Timer that calls the given 'function' every
    'interval' seconds.
  """
  def __init__(self, interval, function, *args, **kwargs):
    self._timer = None
    self.active = False
    self.lock = Lock()
    self.interval = interval
    self.function = function
    self.args = args
    self.kwargs = kwargs
    self.is_running = False
    self.start()

  """
    This method is called when the timer expires.
  """
  def _run(self):
    with self.lock:
      self.is_running = False
      if self.active:
        now = time()
        self.next_call += self.interval
        sleep_time = self.next_call - now
        if sleep_time < 0:
          self.next_call = math.ceil((now - self.next_call)/self.interval) * self.interval + self.next_call
          sleep_time = self.next_call - now
        self._timer = Timer(sleep_time, self._run)
        self._timer.start()
        self.is_running = True
    self.function(*self.args, **self.kwargs)
  
    
  """
    This method starts the Repeated Timer functionality. 
  """
  def start(self):
    with self.lock:
      self.active = True
      now = time()
      self.next_call = now + self.interval
      self._timer = Timer(0.001, self._run) # Schedule 1-st call almost 
      self._timer.start()                   # immediately
      self.is_running = True


  """
    This method stops the Repeated Timer functionality.
  """
  def stop(self):
    with self.lock:
      self._timer.cancel()
      self.is_running = False
      self.active = False

"""

  RepeatedRealTimer class helps to schedule or stop repeated calls to 
  a function. It differs from the RepeatedTimer in that its events 
  are synched to the real clock time. For example, you can schedule 
  events to fire every minute exactly at the top of a clock minute.

"""

class RepeatedRealTimer:
  
  """
    Create a new Repeated Real Timer that calls the given 'function' every
    time the "real" clock hits the 'interval' seconds mark. For instance, 
    if the 'inteval' is 10 seconds, the timer will call the 'function'
    at hh:mm:00, hh:mm:10, hh:mm:20, ...
  """
  def __init__(self, interval, function, *args, **kwargs):
    self._timer = None
    self.active = False
    self.lock = Lock()
    self.interval = interval
    self.function = function
    self.args = args
    self.kwargs = kwargs
    self.is_running = False
    self.start()

  """
    This method is called when the timer expires.
  """
  def _run(self):
    self.function(*self.args, **self.kwargs) # Call the external function
    with self.lock:
      self.is_running = False
      if self.active:
        now = time()
        q, r = divmod(now, self.interval)
        self._timer = Timer(self.interval - r, self._run)
        self._timer.start()
        self.is_running = True

  """
    This method starts the Repeated Timer functionality. 
  """
  def start(self):
    with self.lock:
      self.active = True
      now = time()
      q, r = divmod(now, self.interval)
      self._timer = Timer(self.interval - r, self._run)
      self._timer.start()
      self.is_running = True

  """
    This method stops the Repeated Timer functionality.
  """
  def stop(self):
    with self.lock:
      self._timer.cancel()
      self.is_running = False
      self.active = False


"""

  Classes Button and ButtonController are for managing buttons and
  recognizing short clicks and long clicks of the buttons by users. 

"""

"""
  Maintain the button identity and status.
"""

class Button:
  
  def __init__(self, button_id, pin_number, callback_short, callback_long):
    self.button_id        = button_id
    self.pin_number       = pin_number
    self.callback_short   = callback_short
    self.callback_long    = callback_long
    self.status           = "UP"
    self.status_time      = dt.datetime.utcnow()
    self.momentary_status = "UP"
    self.momentary_status_time = dt.datetime.utcnow()
    
  def pressed(self):
    if self.momentary_status == "UP":
      self.momentary_status = "DOWN"
      self.momentary_status_time = dt.datetime.utcnow()
      
  def released(self):
    if self.momentary_status == "DOWN":
      self.momentary_status = "UP"
      self.momentary_status_time = dt.datetime.utcnow()
  
"""
  Poll the button status, recognize and filter out switching jitters
  and detect if the button was pressed or released.
"""

class ButtonController:
  
  def __init__(self):
    GPIO.setmode(GPIO.BCM)
    self.status = "OK"
    self.buttons = {}
    self.timer = RepeatedTimer(0.05, self.check_transitions)
    
  """
    Release resources.
  """  
  def destroy(self):
    self.status = ""
    self.buttons = {}
    GPIO.cleanup()    
    
  """
    Register a new button with this controller.
  """
  def add_button(self, b_id, pin, callback_short, callback_long):
    self.buttons[pin] = Button(b_id, pin, callback_short, callback_long)
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.add_event_detect(pin, GPIO.BOTH, callback=self.button_pressed_released)
    
  """
    This method is called by the Repeated Timer every small fraction of
    a second to detect changes in each registered button status and to decide
    if the constiture a short or long click on the button.
  """  
  def check_transitions(self):
    t = dt.datetime.utcnow()
    mismatch = False
    for b in self.buttons.values():
      if b.status != b.momentary_status:
        mismatch = True
        if (t - b.momentary_status_time).microseconds > 100000:
          old_time = b.status_time
          b.status      = b.momentary_status
          b.status_time = b.momentary_status_time
          if b.status == "UP":
            delta = b.status_time - old_time
            if delta.seconds*1000000+delta.microseconds >= 1000000:
              if not b.callback_long is None:
                b.callback_long()
            else:
              if not b.callback_short is None:
                b.callback_short()

    if not mismatch:
      #print("Stopping timer...")
      self.timer.stop()
    
  """
    This is a callback method called by GPIO when a button is pressed or
    released.
  """  
  def button_pressed_released(self, channel):
    if channel in self.buttons:
      b = self.buttons[channel]
      if GPIO.input(channel):
        b.released()
      else:
        b.pressed()
      #print("Starting timer ...")
      self.timer.start()

"""

  Class NetworkInfo is for getting network inormation, specifically the
  status and IP addresses for 'eth0' and 'wlan0' interfaces. 

"""

class NetworkInfo:
  
  def __init__(self):
    self.wlan0 = "inactive"
    self.eth0  = "inactive"
    
  """
    Retrieve status of WLAN and ETH interfaces
  """  
  def update(self):
    
    s = sp.run(["ifconfig"], capture_output=True, text=True).stdout
    wlan0 = "inactive"
    eth0  = "inactive"
    lines = s.split("\n")
    interface = ""
    for src in lines:
      srcv = src.split()
      if len(srcv) >= 1 and srcv[0][-1:] == ":":
        interface = srcv[0][0:-1]
      if len(srcv) >= 2 and srcv[0] == "inet":
        if interface == "wlan0":
          wlan0 = srcv[1]
        elif interface == "eth0":
          eth0 = srcv[1]
    self.wlan0 = wlan0
    self.eth0 = eth0
    
    if not (self.wlan0 == "inactive"):
      s = sp.run(["cat", "/var/lib/misc/dnsmasq.leases"], capture_output=True, text=True).stdout
      lines = s.split("\n")
      nof_connections = 0
      for src in lines:
        if len(src.strip()) > 5:
          nof_connections = nof_connections + 1
          
      self.wlan0 = self.wlan0 + "  (" + str(nof_connections) + ")"
          
  def get_status(self, interface):
    if interface == "wlan0":
      return interface + ": " + self.wlan0
    elif interface == "eth0":
      return interface + ": " + self.eth0
    else:
      return interface + ": unknown"
    
"""

  Class ChronyInfo is for getting some status information from the
  Chrony server. This status includes the currently used time source
  (which should be 'GPPS' for the GPS pulse source) and the standard
  deviation for the source offset).

"""
      
class ChronyInfo:
  
  def __init(self):
    self.source_id = ""
    self.source_offset = 0
    self.source_deviation = 0
    
  def update(self):
    s = sp.run(["chronyc", "-c", "tracking"], capture_output=True, text=True).stdout
    self.source_id = s.split(",")[1]
    self.source_offset = 0
    self.source_deviation = 0
    if self.source_id != "":
      s = sp.run(["chronyc", "-c", "sourcestats"], capture_output=True, text=True).stdout
      lines = s.split("\n")
      for src in lines:
        srcv = src.split(",")
        if srcv[0] == self.source_id:
          self.source_offset = srcv[6]
          self.source_deviation = srcv[7]
          break
          
  def get_offset(self):
    return self.float_time(self.source_offset)        
          
  def get_deviation(self):
    return self.float_time(self.source_deviation)
          
  def float_time(self, f):
    (sign, digits, exponent) = Decimal(f).as_tuple()
    exponent = exponent + len(digits) - 1
    n = 1
    if abs(exponent) % 3 != 0:
      exponent -= 1
      n += 1
    if abs(exponent) % 3 != 0:
      exponent -= 1
      n += 1
    value = "".join(map(str,digits[0:n]))
    if exponent == 0:
      u = "s"
    elif exponent == -3:
      u = "ms"
    elif exponent == -6:
      u = "us"
    elif exponent == -9:
      u = "ns"
    elif exponent > 0:
      u = ""
      value = "large"
    elif exponent < -9:
      u = ""
      value = ""
    return value + u
    
"""

  Class GPSAPI is for getting GPS information using the shared memory
  technique. It includes the lock type, the number of used satellites,
  lattitude, longitude and altitude. The longitude and latitude is 
  converted to the maidenhead grid before being displayed on the screen.

"""

class GPSAPI:
  
  def __init__(self):
    self.shm = gpsdshm.Shm()
    self.fix = "  "
    self.max_error = 1000
    self.latitude  = 0.0
    self.longitude = 0.0
    self.altitude  = 0.0
    self.epoch     = 0.0
    self.tz_name   = ""
    self.tz_abbrev = ""
    self.tz_daylight = ""
    self.tz_offset = ""
    self.tz_grid   = ""
    self.tf = TimezoneFinder()
    self.declination = ""
    
  def update(self):
    self.shm.online
    
    if self.shm.fix.mode == 0:
      self.fix = "  "
    elif self.shm.fix.mode == 1:
      self.fix = "--"
    elif self.shm.fix.mode == 2:
      self.fix = "2D"
    elif self.shm.fix.mode == 3:
      self.fix = "3D"    
      self.epoch = self.shm.fix.time
      
    if self.fix == "2D" or self.fix == "3D":
      try:
        self.latitude  = float(self.shm.fix.latitude)
        self.longitude = float(self.shm.fix.longitude)
        self.altitude  = float(self.shm.fix.altitude)
      except:
        pass
      
    n = self.shm.satellites_visible
    self.ss_max = 0
    self.sats_visible = 0
    self.sats_inuse = 0
    self.sats = []
    for i in range(n):
      try: 
        ss = self.shm.satellites[i].ss
        if ss > self.ss_max:
          self.ss_max = ss
        u  = self.shm.satellites[i].used
        if ss > 0:
          self.sats_visible += 1
          if u:
            self.sats_inuse += 1
      except:
        ss = 0
        u = False
      
      self.sats.append( (ss, u) )
      
    try:   
      if self.shm.fix.epx > self.shm.fix.epy:
        self.max_err = int(self.shm.fix.epx + 1)
      else:
        self.max_err = int(self.shm.fix.epy + 1)
    except:
      pass
      
  def maidenhead(self):
    
    if self.shm.fix.mode == 3:
      latitude  = self.latitude
      longitude = self.longitude
      altitude  = self.altitude
      
      L1 = "ABCDEFGHIJKLMNOPQR"
      L2 = "0123456789"
      L3 = "ABCDEFGHIJKLMNOPQRSTUVWX"
      L4 = "0123456789"
      L5 = "ABCDEFGHIJKLMNOPQRSTUVWX"
    
      lo1 = int((longitude + 180.0) / 20.)
      if lo1 < 0:
        lo1 = 0
      elif lo1 > 17:
        lo1 = 17
      
      lo1rem = (longitude + 180.0) - lo1 * 20.0
      
      lo2 = int(lo1rem / 2.0)
      if lo2 < 0:
        lo2 = 0
      elif lo2 > 9:
        lo2 = 9
        
      lo2rem = lo1rem - lo2 * 2.0
      
      lo3 = int(lo2rem * 12.0)
      if lo3 < 0:
        lo3 = 0
      elif lo3 > 23:
        lo3 = 23
      
      lo3rem = lo2rem * 12.0 - lo3
      
      lo4 = int(lo3rem * 10)
      if lo4 < 0:
        lo4 = 0
      elif lo4 > 9:
        lo4 = 9
      
      la1 = int((latitude + 90.0) / 10.)
      if la1 < 0:
        la1 = 0
      elif la1 > 17:
        la1 = 17
      
      la1rem = (latitude + 90.0) - la1 * 10.0
      
      la2 = int(la1rem)
      if la2 < 0:
        la2 = 0
      elif la2 > 9:
        la2 = 9
        
      la2rem = la1rem - la2
      
      la3 = int(la2rem * 24.0)
      if la3 < 0:
        la3 = 0
      elif la3 > 23:
        la3 = 23
      
      la3rem = la2rem * 24.0 - la3
      la4 = int(la3rem * 10)
      if la4 < 0:
        la4 = 0
      elif la4 > 9:
        la4 = 9
      
      grid = L1[lo1] + L1[la1] + str(lo2) + str(la2) + L3.lower()[lo3] + L3.lower()[la3] + str(lo4) + str(la4)
      # Re-calculate time zone info and magnetic declinition
      if grid != self.tz_grid:
        print("Computing time zone info / magnetic declinition ...")     
        timezone_str = self.tf.timezone_at(lat=latitude, lng=longitude)
    
        if not timezone_str is None:
          self.tz_name = timezone_str
          
          tz = None
          try:
            tz = pytz.timezone(timezone_str)
          except pytz.exceptions.UnknownTimeZoneError:
            pass
          
          if not tz is None:
            current_localized = tz.localize(dt.datetime.utcnow())
            self.tz_daylight  = current_localized.dst()
            self.tz_offset    = current_localized.strftime("%z")
            self.tz_abbrev    = current_localized.strftime("%Z")
            if self.tz_abbrev[0:1] == "+" or self.tz_abbrev[0:1] == "-":
              self.tz_abbrev = ""
          else:
            self.tz_abbrev = ""
            self.tz_name   = ""
            self.tz_daylight = False
            self.tz_offset = ""
        year = dt.datetime.utcnow().year
        if year > 2025:
          year = 2025
        mag = pyIGRF.igrf_value(latitude, longitude, altitude/1000.0, year)
        if mag[0] >= 0:
          self.declination = "E" + str(round(mag[0],1)) + "\u00b0"
        else:
          self.declination = "W" + str(round(-mag[0],1)) + "\u00b0"
      self.tz_grid = grid
      return grid
    else:
      self.tz_grid = ""
      self.tz_abbrev = ""
      self.tz_name   = ""
      self.tz_daylight = False
      self.tz_offset = ""
      self.declination = ""
      return "--------"
      
"""
  
  Class Menu is used to display icons for the buttons and define actions to be
  taken when these buttons are pressed.
  
"""

class Menu:
  
  
  """
    To create a menu the file names containing images of the icons to
    be displayed for each button have to be specified. If one of the 
    buttons is not used an "empty" icon should be specified.
    Also, for each button 2 action functions are provided. One for
    a regular click of the button and one for a long click. Some 
    functions can be specified as None if no action is supposed to
    happen when this button is clicked (or long-clicked).
  """
  def __init__(self, icon_top_file, icon_middle_file, icon_bottom_file,
               top_pressed_short, top_pressed_long,
               middle_pressed_short, middle_pressed_long, 
               bottom_pressed_short, bottom_pressed_long):
    self.icon_top    = Image.open(icon_top_file)
    self.icon_middle = Image.open(icon_middle_file)
    self.icon_bottom = Image.open(icon_bottom_file)
    self.action_on_top_pressed_short = top_pressed_short
    self.action_on_top_pressed_long = top_pressed_long
    self.action_on_middle_pressed_short = middle_pressed_short
    self.action_on_middle_pressed_long = middle_pressed_long
    self.action_on_bottom_pressed_short = bottom_pressed_short
    self.action_on_bottom_pressed_long = bottom_pressed_long
    
  def get_icons(self):
    return (self.icon_top, self.icon_middle, self.icon_bottom)

"""

  Class DspInfo is for controlling the eInk screen. 

"""

class DspInfo:
  
  """
    To create the display controller it needs to be passed a function which, if called,
    return the currently active menu object. This object is used to determine what icons
    should be drawn against each button.
  """
  def __init__(self, get_current_menu_function):
    try:
      self.epd = epd2in13.EPD()
      self.update_type = "FULL"
      self.epd.init(self.epd.FULL_UPDATE)
      self.font12 = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 12)
      self.font18 = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 18)
      self.font24 = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 24)
      self.font32 = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 32)
      self.picture      = Image.open("screen-saver.png")
      self.sleeping     = Image.open("shutting-down.png")
      
      self.image = Image.new('1', (epd2in13.EPD_HEIGHT, epd2in13.EPD_WIDTH), 255)
      self.draw  = ImageDraw.Draw(self.image)
      
      self.get_current_menu_function = get_current_menu_function
      
      self.one_time_refresh = True
      
      self.status = "OK"
    except:
      self.status = "ERR"
      print('traceback.format_exc():\n%s',traceback.format_exc())
      
      
  def show(self, text, font, x, y, w, h):
    try:
      self.draw.rectangle((x, y, w,h), fill = 255)
      self.draw.text((x, y), text, font = font, fill = 0)
      newimage = self.image.crop([x, y, w, h])
      self.image.paste(newimage, (x, y))  
    except:
      pass
      
  def clear(self):
    self.draw.rectangle((0, 0, epd2in13.EPD_HEIGHT, epd2in13.EPD_WIDTH), fill=255) 
      
  def set_one_time_refresh(self):
    self.one_time_refresh = True
      
  def show_final(self):
    try:
      if self.one_time_refresh and self.update_type == "PART":
        self.epd.init(self.epd.FULL_UPDATE)
        self.update_type = "FULL"

      self.epd.displayPartial(self.epd.getbuffer(self.image))
      
      if self.one_time_refresh and self.update_type == "FULL":
        self.epd.init(self.epd.PART_UPDATE)
        self.update_type = "PART"
        self.one_time_refresh = False
    except:
      pass
      
  def show_final_full(self):
    self.epd.init(self.epd.FULL_UPDATE)
    self.epd.displayPartial(self.epd.getbuffer(self.image))
    
  def mag_declination(self, gpsapi):
    if gpsapi.declination == "":
      d = ""
    else:
      d = gpsapi.declination[0:1]
    self.show(gpsapi.declination[1:], self.font12, 55, 0, 95, 8)
    self.show(d, self.font12, 69, 15, 80, 23)

  def gps_status_graphics(self, fix, inuse):
    n = inuse
    if n > 12:
      n = 12
    n = n * 30
    if n > 0:
      n = n - 1
    s = 270
    e = s + n
    if e > 360:
      e = e - 360
    self.draw.rectangle(((10, 0), (50,40)), fill=255)
    self.draw.pieslice(((10,0), (50,40)), start=s, end=e, fill = 0)
    self.draw.ellipse(((14,4), (46,36)), fill=255, outline=0)
    self.draw.text((20,10), str(fix), font=self.font18, fill=0)
        
  def max_error(self, maxerr):
    if maxerr > 99:
      smax = "==="
    else:
      smax = "~" + str(int(maxerr))
      
    self.show(smax, self.font18, 150, 10, 210, 30)
      
  def maidenhead(self, mh):
    self.show(mh, self.font24, 95, 0, 205, 26)
      
  def altitude(self, alt):
    if alt >= 0:
      s_alt = "+" + str(int(alt))
    else:
      s_alt = "-" + str(int(alt))
    self.show(s_alt.rjust(7) + "m", self.font24, 94, 27, 205, 57)
    
  def date_time(self, gpsapi):
    t = dt.datetime.utcnow()
    abbrev = gpsapi.tz_abbrev.ljust(4) + " " + '\u2600'
    self.show(abbrev, self.font12, 75, 54, 114, 69)
    offs = gpsapi.tz_offset
    self.show(offs[0:3] + ":" + offs[3:5], self.font12, 75, 70, 114, 82)
    hhmm = t.strftime("%H%M")
    self.show(":", self.font32, 154, 52, 165, 82)
    self.show(hhmm[0:2], self.font32, 120, 52, 159, 82)
    self.show(hhmm[2:4], self.font32, 170, 52, 205, 82)
    self.show(t.strftime("%a %b %d"), self.font18, 95, 85, 200, 100)
    
  def ntp_status(self, source_id, deviation):
    self.draw.rectangle(((5, 52), (65,102)), fill=255, outline=0)
    self.draw.text((8,57), str(source_id).ljust(5)[0:5], font=self.font18, fill=0)
    self.draw.text((8,77), deviation.rjust(5), font=self.font18, fill=0)
    
  def button_icons(self):
    menu  = self.get_current_menu_function()
    icons = menu.get_icons()
    self.image.paste(icons[0], (210,0))
    self.image.paste(icons[1], (210,40))
    self.image.paste(icons[2], (210,80))
    
  def status_line(self, status):
    self.show(status, self.font12, 0, 105, 185, 112)
    
  def message(self, msg):
    self.show(msg, self.font12, 135, 0, 250, 15)
    
  def show_picture(self):
    self.image.paste(self.picture, (0,0))
    
  def show_sleeping(self):
    self.image.paste(self.sleeping, (0,0))

"""

  Class Monitor encapsulates the high-level logic of the application.

"""

class Monitor:
  
  def __init__(self):
    self.lock = Lock()
    self.in_progress = False
    
    self.gpsapi  = GPSAPI()
    self.chrony  = ChronyInfo()
    self.display = DspInfo(self.get_current_menu)
    self.netinfo = NetworkInfo()
    self.work_mode = "run"
    self.picture_done = False
    self.shutdown_stage = 0
    
    self.first_3D_received = False
    
    self.wifi_mode = "AP"
    
    self.n_refresh = 0
    
    self.action_event = Event()
    self.bc = ButtonController()
    self.bc.add_button("TOP",   26, self.top_pressed, self.top_pressed_long)
    self.bc.add_button("MIDDLE", 6, self.middle_pressed, self.middle_pressed_long)
    self.bc.add_button("BOTTOM", 5, self.bottom_pressed, self.bottom_pressed_long)
    
    self.main_menu    = Menu("refresh.png", "wifi.png", "power.png",
                             self.action_refresh, self.action_sleep,  
                             self.action_wifi_select, None, 
                             self.action_power, self.action_exit_confirm)
                             
    self.sleep_menu   = Menu("alarm-clock.png", "empty.png", "power.png", 
                             self.action_wakeup, None, None, None, self.action_power, None)
                             
    self.power_menu   = Menu("shutdown.png", "back.png", "empty.png",
                             self.action_shutdown, None, self.action_cancel_shutdown, None, None, None)
                             
    self.exit_menu   = Menu("exit.png", "back.png", "empty.png",
                             self.action_exit, None, self.action_cancel_exit, None, None, None)
                             
    self.wifi_menu   = Menu("home-wifi.png", "field-wifi.png", "no-wifi.png",
                             self.action_wifi_home, None, self.action_wifi_field, None, self.action_wifi_off, None)
                             
    self.current_menu = self.main_menu
    self.current_menu_changed = False
    
    self.repeated_real_timer = RepeatedRealTimer(5.0, self.poll_info)
    
  """
    This method is called repeatedly to retrieve the new information and
    display it of the screen.
  """
  def poll_info(self):
    self.action_event.set()
    
  """
    This method can be called to determine what is a current menu object.
  """
  def get_current_menu(self):
    self.current_menu_changed = False
    return self.current_menu
    
    
  """
    This method sets a new menu as the active one.
  """
  def set_current_menu(self, menu):
    self.current_menu = menu
    self.current_menu_changed = True
    
  """
    "Refresh" action: we perform a full refresh of the display.
  """  
  def action_refresh(self):
    if self.work_mode == "run":
      self.display.set_one_time_refresh()
      self.action_event.set()
  
  """
    "Sleep" action: we switch our display to the sleep mode.
  """
  def action_sleep(self):
    if self.work_mode == "run":
      self.work_mode = "sleep"
      self.set_current_menu(self.sleep_menu)
      self.action_event.set()

  """
    "Cancel WiFi selection" action
  """
  def action_cancel_wifi_select(self):
    self.set_current_menu(self.main_menu)
    self.action_event.set()
      
  """
    "WiFi selection" action
  """  
  def action_wifi_select(self):
    if self.work_mode == "run" or self.work_mode == "sleep":
      if self.wifi_mode == "AP":
        self.wifi_menu = Menu("home-wifi.png", "back.png", "no-wifi.png",
                           self.action_wifi_home, None, self.action_cancel_wifi_select, None, self.action_wifi_off, None)
      elif self.wifi_mode == "CLIENT":
        self.wifi_menu = Menu("back.png", "field-wifi.png", "no-wifi.png",
                           self.action_cancel_wifi_select, None, self.action_wifi_field, None, self.action_wifi_off, None)
      else:
        self.wifi_menu = Menu("home-wifi.png", "field-wifi.png", "back.png",
                           self.action_wifi_home, None, self.action_wifi_field, None, self.action_cancel_wifi_select, None)
      self.set_current_menu(self.wifi_menu)
      self.action_event.set()

      
  """
    "WiFi Home" action: turning ON client WiFi mode
  """
  def action_wifi_home(self):
    if self.work_mode == "run":
      
      cmd = [ "sudo", "/home/pi/set_wifi.sh", "CLIENT" ]
      p = sp.Popen(cmd,
                   shell=False,
                   stdin=None,
                   stdout=None,
                   stderr=None,
                   close_fds=True)
      self.wifi_mode = "CLIENT"
      self.set_current_menu(self.main_menu)
      self.action_event.set()
      
  """
    "WiFi Field" action: turning ON access point mode
  """
  def action_wifi_field(self):
    if self.work_mode == "run":
      cmd = [ "sudo", "/home/pi/set_wifi.sh", "AP" ]
      p = sp.Popen(cmd,
                   shell=False,
                   stdin=None,
                   stdout=None,
                   stderr=None,
                   close_fds=True)
      self.wifi_mode = "AP"
      self.set_current_menu(self.main_menu)
      self.action_event.set()
      
  """
    "WiFi OFF" action: turning OFF WiFi
  """
  def action_wifi_off(self):
    if self.work_mode == "run":
      cmd = [ "sudo", "/home/pi/set_wifi.sh", "OFF" ]
      p = sp.Popen(cmd,
                   shell=False,
                   stdin=None,
                   stdout=None,
                   stderr=None,
                   close_fds=True)
      self.wifi_mode = "OFF"
      self.set_current_menu(self.main_menu)
      self.action_event.set()
    
  """
    "Power" action: we switch to a shutdown confirmation menu.
  """  
  def action_power(self):
    if self.work_mode == "run" or self.work_mode == "sleep":
      self.set_current_menu(self.power_menu)
      self.action_event.set()
    
  """
    Exit" action: we switch to an exit confirmation menu.
  """
  def action_exit_confirm(self):
    print("action_exit")
    if self.work_mode == "run" or self.work_mode == "sleep":
      self.set_current_menu(self.exit_menu)
      self.action_event.set()
      
  """
    "Wake Up" action: we start displaying the device information again.
  """
  def action_wakeup(self):
    if self.work_mode == "sleep":
      self.work_mode = "run"
      self.set_current_menu(self.main_menu)
      self.display.set_one_time_refresh()
      self.action_event.set()
    
  """
    "Shutdown" action: we initiate the shutdown.
  """
  def action_shutdown(self):
    if self.work_mode == "run" or self.work_mode == "sleep":
      self.shutdown_stage = 1
      self.action_event.set()
      
  """
    "Cancel shutdown" action: we return to the main menu.
  """
  def action_cancel_shutdown(self):
    if self.work_mode == "run":
      self.set_current_menu(self.main_menu)
      self.action_event.set()
    elif self.work_mode == "sleep":
      self.set_current_menu(self.sleep_menu)
      self.action_event.set()
      
  """
    "Exit" action: we exit from the script.
  """
  def action_exit(self):
    if self.work_mode == "run" or self.work_mode == "sleep":          
      self.work_mode = "stop"
      self.action_event.set()
      
  """
    "Cancel exit" action: we return to the main menu.
  """
  def action_cancel_exit(self):
    if self.work_mode == "run":
      self.set_current_menu(self.main_menu)
      self.action_event.set()
    elif self.work_mode == "sleep":
      self.set_current_menu(self.sleep_menu)
      self.action_event.set()
      
  """
    Call the correct function when the top button is pressed.
  """
  def top_pressed(self):
    print("Pressed TOP ...") 
    if not self.current_menu.action_on_top_pressed_short is None: 
      self.current_menu.action_on_top_pressed_short()
    
  """
    Call the correct function when the top button is long-pressed.
  """
  def top_pressed_long(self):
    print("Pressed TOP long ...")  
    if not self.current_menu.action_on_top_pressed_long is None:
      self.current_menu.action_on_top_pressed_long()
  
  """
    Call the correct function when the middle button is pressed.
  """  
  def middle_pressed(self):
    print("Pressed MIDDLE ...")
    if not self.current_menu.action_on_middle_pressed_short is None:
      self.current_menu.action_on_middle_pressed_short()
    
  """
    Call the correct function when the middle button is long-pressed.
  """
  def middle_pressed_long(self):
    print("Pressed MIDDLE long ...")
    if not self.current_menu.action_on_middle_pressed_long is None:
      self.current_menu.action_on_middle_pressed_long()
    
  """
    Call the correct function when the bottom button is pressed.
  """  
  def bottom_pressed(self):
    print("Pressed BOTTOM ...")
    if not self.current_menu.action_on_bottom_pressed_short is None:
      self.current_menu.action_on_bottom_pressed_short()
  
  """
    Call the correct function when the bottom button is long-pressed.
  """  
  def bottom_pressed_long(self):
    print("Pressed BOTTOM long ...")
    if not self.current_menu.action_on_bottom_pressed_long is None:
      self.current_menu.action_on_bottom_pressed_long()
  
  """
    This methid is waiting until a certain action is detected or until
    a timeout occurrs.
  """  
  def wait_for_action_or_time(self):
    self.action_event.wait()
    self.action_event.clear()
    
  """
    This methid takes care of refreshing the display.
  """  
  def refresh_info(self):
    """
      Only one thread can enter this method.
    """
    self.lock.acquire()
    if self.in_progress:
      go = False
    else:
      go = True
    self.in_progress = True
    self.lock.release()
    if not go:
      return
    
    self.n_refresh += 1    # Number of refresh requests
    
    if self.n_refresh == 1:
      print("Stating refresh ... " + str(time()))
    
    """
      In the run mode all information is updated.
    """
    if self.work_mode == "run":
      self.gpsapi.update()
      self.chrony.update()
      self.netinfo.update()
      
      """
        If this is the first time for detecting a 3D fix from the GPS while the monitor script is running, 
        adjust RPi clock to the GPS time. It might be a while until NTP server will update the clock and
        until that time the clock does not have correct value.
      """
      if not self.first_3D_received:
        if self.gpsapi.fix == "3D":
          self.first_3D_received = True
          cur_time_str = dt.datetime.fromtimestamp(self.gpsapi.epoch).strftime('%Y/%m/%d %H:%M:%S')
          print("Adjusting initial date and time to " + cur_time_str)
          sp.run(['sudo', 'date', '-s', '{:}'.format(cur_time_str)], capture_output=True, text=True).stdout
    
      if self.n_refresh == 1:
        print("All updates passed OK. About to call display.clear() ...")
    
      self.display.clear() 
      
      if self.n_refresh == 1:
        print("... display.clear() completed successfully.")
    
      self.display.button_icons()
      self.display.gps_status_graphics(self.gpsapi.fix, self.gpsapi.sats_inuse)
      self.display.ntp_status(self.chrony.source_id, self.chrony.get_deviation())
      self.display.maidenhead(self.gpsapi.maidenhead())
      self.display.altitude(self.gpsapi.altitude)
      self.display.mag_declination(self.gpsapi)
      self.display.date_time(self.gpsapi)
      if self.shutdown_stage == 0:
        self.display.status_line(self.netinfo.get_status("wlan0"))
      else:
        self.display.status_line("Shutting down ...")
        self.shutdown_stage = 2
      self.display.show_final()
    
      self.picture_done = False
    
      """
        There are some special cases when we need small updates on the display 
        even if we are in the "sleep" mode.
      """
    elif self.work_mode == "sleep":
      
      if self.current_menu_changed or not self.picture_done or self.shutdown_stage == 1:
        
        if self.shutdown_stage == 1:
          self.display.show_sleeping()
          self.shutdown_stage = 2
        else:
          self.display.show_picture()
          print("About to refresh menu icons ...")
          self.display.button_icons()
      
        self.display.show_final_full()
        self.picture_done = True
        
    #print("Ending refresh method... " + str(time()))  
    self.in_progress = False
    
    if self.shutdown_stage == 2:
      self.work_mode = "stop"
  
  
  """
    This method decides if the work can be continued
  """    
  def continue_work(self):
    if self.work_mode == "stop":
      return False
    else:
      return True
      
  """
    Release all resources
  """    
  def destroy(self):
    self.repeated_real_timer.stop()
    self.bc.destroy()
    

"""

  Run the main logic.

"""

m = Monitor()

while m.continue_work():
  m.wait_for_action_or_time()
  m.refresh_info()
  
if m.shutdown_stage == 2:
  sp.run(["sudo", "shutdown", "-h", "now"], capture_output=True, text=True).stdout
  print("Issuing SHUTDOWN ...")
  
m.destroy()
