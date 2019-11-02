#!/usr/bin/env python

# Plotcontrol plots the time course of control values
#
# This software is part of the EEGsynth project, see <https://github.com/eegsynth/eegsynth>.
#
# Copyright (C) 2017-2019 EEGsynth project
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

from pyqtgraph.Qt import QtGui, QtCore
import configparser
import redis
import argparse
import numpy as np
import os
import pyqtgraph as pg
import sys
import time
import signal
import math
from scipy.interpolate import interp1d
from scipy.signal import butter, lfilter

if hasattr(sys, 'frozen'):
    path = os.path.split(sys.executable)[0]
    file = os.path.split(sys.executable)[-1]
elif sys.argv[0] != '':
    path = os.path.split(sys.argv[0])[0]
    file = os.path.split(sys.argv[0])[-1]
else:
    path = os.path.abspath('')
    file = os.path.split(path)[-1] + '.py'

# eegsynth/lib contains shared modules
sys.path.insert(0, os.path.join(path, '../../lib'))
import EEGsynth
import FieldTrip

parser = argparse.ArgumentParser()
parser.add_argument("-i", "--inifile", default=os.path.join(path, os.path.splitext(file)[0] + '.ini'), help="optional name of the configuration file")
args = parser.parse_args()

config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
config.read(args.inifile)

try:
    r = redis.StrictRedis(host=config.get('redis', 'hostname'), port=config.getint('redis', 'port'), db=0, charset='utf-8', decode_responses=True)
    response = r.client_list()
except redis.ConnectionError:
    raise RuntimeError("cannot connect to Redis server")

# combine the patching from the configuration file and Redis
patch = EEGsynth.patch(config, r)

# this can be used to show parameters that have changed
monitor = EEGsynth.monitor()

# get the options from the configuration file
debug = patch.getint('general', 'debug')
delay       = patch.getfloat('general', 'delay')
historysize = patch.getfloat('general', 'window') # in seconds
secwindow   = patch.getfloat('general', 'window')
winx        = patch.getfloat('display', 'xpos')
winy        = patch.getfloat('display', 'ypos')
winwidth    = patch.getfloat('display', 'width')
winheight   = patch.getfloat('display', 'height')

historysize = int(historysize/delay) # in steps

input_name, input_variable = list(zip(*config.items('input')))
ylim_name, ylim_value = list(zip(*config.items('ylim')))

# count total number of curves to be drawm
curve_nrs = 0
for i in range(len(input_name)):
    temp = input_variable[i].split(",")
    for ii in range(len(temp)):
        curve_nrs += 1

# initialize graphical window
app = QtGui.QApplication([])
win = pg.GraphicsWindow(title="EEGsynth plotcontrol")
win.setWindowTitle('EEGsynth plotcontrol')
win.setGeometry(winx, winy, winwidth, winheight)

# Enable antialiasing for prettier plots
pg.setConfigOptions(antialias=True)

# Initialize variables
inputhistory = np.ones((curve_nrs, historysize))
inputplot    = []
inputcurve   = []

# Create panels for each channel
for iplot, name in enumerate(input_name):

    inputplot.append(win.addPlot(title="%s" % name))
    inputplot[iplot].setLabel('bottom', text = 'Time (s)')
    inputplot[iplot].showGrid(x=False, y=True, alpha=0.5)

    ylim = patch.getfloat('ylim', name, multiple=True, default=None)
    print ylim
    if ylim==[] or ylim==None:
        print("Ylim empty, will let it flow")
    else:
        print("Setting Ylim according to specified range")
        inputplot[iplot].setYRange(ylim[0], ylim[1])

    temp = input_variable[iplot].split(",")
    for icurve in range(len(temp)):
        linecolor = patch.getstring('linecolor', name, multiple=True, default='w')
        inputcurve.append(inputplot[iplot].plot(pen=linecolor[icurve]))

    win.nextRow()

def update():
    global inputhistory

    # shift all historic data with one sample
    inputhistory = np.roll(inputhistory, -1, axis=1)

    # update with current data
    counter = 0
    for iplot in range(len(input_name)):

       input_variable_list = input_variable[iplot].split(",")

       for ivar in range(len(input_variable_list)):
            try:
                inputhistory[counter, historysize-1] = r.get(input_variable_list[ivar])
            except:
                inputhistory[counter, historysize-1] = np.nan

            # time axis
            timeaxis = np.linspace(-secwindow, 0, historysize)

            # update timecourses
            inputcurve[counter].setData(timeaxis, inputhistory[counter, :])
            counter += 1


# keyboard interrupt handling
def sigint_handler(*args):
    QtGui.QApplication.quit()


signal.signal(signal.SIGINT, sigint_handler)

# Set timer for update
timer = QtCore.QTimer()
timer.timeout.connect(update)
timer.setInterval(10)            # timeout in milliseconds
timer.start(int(delay * 1000))   # in milliseconds

# Start
QtGui.QApplication.instance().exec_()
