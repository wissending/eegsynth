#!/usr/bin/env python

# This module writes Redis control values and events to MIDI.
#
# This software is part of the EEGsynth project, see https://github.com/eegsynth/eegsynth
#
# Copyright (C) 2017-2018 EEGsynth project
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import ConfigParser # this is version 2.x specific, on version 3.x it is called "configparser" and has a different API
import argparse
import mido
import os
import redis
import sys
import threading
import time

if hasattr(sys, 'frozen'):
    basis = sys.executable
elif sys.argv[0]!='':
    basis = sys.argv[0]
else:
    basis = './'
installed_folder = os.path.split(basis)[0]

# eegsynth/lib contains shared modules
sys.path.insert(0, os.path.join(installed_folder,'../../lib'))
import EEGsynth

parser = argparse.ArgumentParser()
parser.add_argument("-i", "--inifile", default=os.path.join(installed_folder, os.path.splitext(os.path.basename(__file__))[0] + '.ini'), help="optional name of the configuration file")
args = parser.parse_args()

config = ConfigParser.ConfigParser()
config.read(args.inifile)

try:
    r = redis.StrictRedis(host=config.get('redis','hostname'), port=config.getint('redis','port'), db=0)
    response = r.client_list()
except redis.ConnectionError:
    print "Error: cannot connect to redis server"
    exit()

# combine the patching from the configuration file and Redis
patch = EEGsynth.patch(config, r)

# this determines how much debugging information gets printed
debug = patch.getint('general','debug')

midichannel = patch.getint('midi', 'channel')-1  # channel 1-16 get mapped to 0-15
outputport = EEGsynth.midiwrapper(config)
outputport.open_output()

# the scale and offset are used to map Redis values to MIDI values
input_scale  = patch.getfloat('input', 'scale', default=127)
input_offset = patch.getfloat('input', 'offset', default=0)

# this is to prevent two messages from being sent at the same time
lock = threading.Lock()

class TriggerThread(threading.Thread):
    def __init__(self, redischannel, note):
        threading.Thread.__init__(self)
        self.redischannel = redischannel
        self.note = note
        self.running = True
    def stop(self):
        self.running = False
    def run(self):
        pubsub = r.pubsub()
        pubsub.subscribe('OUTPUTMIDI_UNBLOCK')  # this message unblocks the redis listen command
        pubsub.subscribe(self.redischannel)  # this message contains the note
        while self.running:
            for item in pubsub.listen():
                if not self.running or not item['type'] == 'message':
                    break
                if item['channel']==self.redischannel:
                    # map the Redis values to MIDI values
                    val = EEGsynth.rescale(item['data'], slope=input_scale, offset=input_offset)
                    val = EEGsynth.limit(val, 0, 127)
                    val = int(val)
                    if debug>1:
                        print item['channel'], '=', val
                    if midichannel is None:
                        msg = mido.Message('note_on', note=self.note, velocity=val)
                    else:
                        msg = mido.Message('note_on', note=self.note, velocity=val, channel=midichannel)
                    lock.acquire()
                    outputport.send(msg)
                    lock.release()

trigger_name = []
trigger_code = []
for code in range(1,128):
    trigger_name.append("note%03d" % code)
    trigger_code.append(code)
for name in ['polytouch', 'aftertouch', 'pitchwheel', 'start', 'continue', 'stop', 'reset']:
    trigger_name.append(name)
    trigger_code.append(None)

# each of the notes that can be played is mapped onto a different trigger
trigger = []
for name, code in zip(trigger_name, trigger_code):
    if config.has_option('trigger', name):
        # start the background thread that deals with this note
        this = TriggerThread(patch.getstring('trigger', name), code)
        trigger.append(this)
        if debug>1:
            print name, 'trigger configured'

# start the thread for each of the notes
for thread in trigger:
    thread.start()

control_name = []
control_code = []
for code in range(1,128):
    control_name.append("control%03d" % code)
    control_code.append(code)
for name in ['polytouch', 'aftertouch', 'pitchwheel', 'start', 'continue', 'stop', 'reset']:
    control_name.append(name)
    control_code.append(None)

# control values are only relevant when different from the previous value
previous_val = {}
for name in control_name:
    previous_val[name] = None

try:
    while True:
        time.sleep(patch.getfloat('general', 'delay'))

        for name, code in zip(control_name, control_code):
            # loop over the control values
            val = patch.getfloat('control', name)
            if val is None:
                continue # it should be skipped when not present in the ini or redis
            if val==previous_val[name]:
                continue # it should be skipped when identical to the previous value
            previous_val[name] = val
            # map the Redis values to MIDI values
            val = EEGsynth.rescale(val, slope=input_scale, offset=input_offset)
            val = EEGsynth.limit(val, 0, 127)
            val = int(val)
            if midichannel is None:
                msg = mido.Message('control_change', control=code, value=val)
            else:
                msg = mido.Message('control_change', control=code, value=val, channel=midichannel)
            if debug>1:
                print code, val, name
            lock.acquire()
            outputport.send(msg)
            lock.release()

except KeyboardInterrupt:
    print "Closing threads"
    for thread in trigger:
        thread.stop()
    r.publish('OUTPUTMIDI_UNBLOCK', 1)
    for thread in trigger:
        thread.join()
    sys.exit()
