#!/usr/bin/env python
# encoding: utf-8

"""@package Operators
This module offers various methods to process eye movement data

Created by Tomas Knapen on 2010-12-19.
Copyright (c) 2010 __MyCompanyName__. All rights reserved.

More details.
"""

import os, sys, subprocess, re
import pickle
import scipy as sp
import numpy as np
import pandas as pd
import numpy.linalg as LA
import matplotlib.pyplot as plt
from math import *
from scipy.signal import butter, lfilter, filtfilt, fftconvolve, resample
import scipy.interpolate as interpolate
from lmfit import minimize, Parameters, Parameter, report_fit
from IPython import embed as shell

from Operator import Operator
import ArrayOperator
# from Tools.other_scripts.savitzky_golay import *

from Tools.other_scripts import functions_jw_GLM

def _butter_lowpass(data, highcut, fs, order=5):
    nyq = 0.5 * fs
    high = highcut / nyq
    b, a = sp.signal.butter(order, high, btype='lowpass')
    y = sp.signal.filtfilt(b, a, data)
    return y

def _butter_highpass(data, lowcut, fs, order=5):
    nyq = 0.5 * fs
    low = lowcut / nyq
    b, a = sp.signal.butter(order, low, btype='highpass')
    y = sp.signal.filtfilt(b, a, data)
    return y

def _butter_bandpass(data, lowcut, highcut, fs, order=5):
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    data_hp = _butter_highpass(data, lowcut, fs, order)
    b, a = sp.signal.butter(order, high, btype='lowpass')
    y = sp.signal.filtfilt(b, a, data_hp)
    return y
    
def detect_saccade_from_data(xy_data = None, vel_data = None, l = 5, sample_rate = 1000.0, minimum_saccade_duration = 0.0075):
    """Uses the engbert & mergenthaler algorithm (PNAS 2006) to detect saccades.
    
    This function expects a sequence (N x 2) of xy gaze position or velocity data. 
    
    Arguments:
        xy_data (numpy.ndarray, optional): a sequence (N x 2) of xy gaze (float/integer) positions. Defaults to None
        vel_data (numpy.ndarray, optional): a sequence (N x 2) of velocity data (float/integer). Defaults to None.
        l (float, optional):determines the threshold. Defaults to 5 median-based standard deviations from the median
        sample_rate (float, optional) - the rate at which eye movements were measured per second). Defaults to 1000.0
        minimum_saccade_duration (float, optional) - the minimum duration for something to be considered a saccade). Defaults to 0.0075
    
    Returns:
        list of dictionaries, which each correspond to a saccade.
        
        The dictionary contains the following items:
            
    Raises:
        ValueError: If neither xy_data and vel_data were passed to the function.
    
    """
    
    # If xy_data and vel_data are both None, function can't continue
    if xy_data is None and vel_data is None:
        raise ValueError("Supply either xy_data or vel_data")    
        
    #If xy_data is given, process it
    if not xy_data is None:
        xy_data = np.array(xy_data)
        # when are both eyes zeros?
        xy_data_zeros = (xy_data == 0.0001).sum(axis = 1)
    
    # Calculate velocity data if it has not been given to function
    if vel_data is None:
        # # Check for shape of xy_data. If x and y are ordered in columns, transpose array.
        # # Should be 2 x N array to use np.diff namely (not Nx2)
        # rows, cols = xy_data.shape
        # if rows == 2:
        #     vel_data = np.diff(xy_data)
        # if cols == 2:
        #     vel_data = np.diff(xy_data.T)
        vel_data = np.zeros(xy_data.shape)
        vel_data[1:] = np.diff(xy_data, axis = 0)
    else:
        vel_data = np.array(vel_data)

    # median-based standard deviation, for x and y separately
    med = np.median(vel_data, axis = 0)
    
    scaled_vel_data = vel_data/np.mean(np.array(np.sqrt((vel_data - med)**2)), axis = 0)
    # normalize and to acceleration and its sign
    if (float(np.__version__.split('.')[1]) == 1.0) and (float(np.__version__.split('.')[1]) > 6):
        normed_scaled_vel_data = LA.norm(scaled_vel_data, axis = 1)
        normed_vel_data = LA.norm(vel_data, axis = 1)
    else:
        normed_scaled_vel_data = np.array([LA.norm(svd) for svd in np.array(scaled_vel_data)])
        normed_vel_data = np.array([LA.norm(vd) for vd in np.array(vel_data)])
    normed_acc_data = np.r_[0,np.diff(normed_scaled_vel_data)]
    signed_acc_data = np.sign(normed_acc_data)
    
    # when are we above the threshold, and when were the crossings
    over_threshold = (normed_scaled_vel_data > l)
    # integers instead of bools preserve the sign of threshold transgression
    over_threshold_int = np.array(over_threshold, dtype = np.int16)
    
    # crossings come in pairs
    threshold_crossings_int = np.concatenate([[0], np.diff(over_threshold_int)])
    threshold_crossing_indices = np.arange(threshold_crossings_int.shape[0])[threshold_crossings_int != 0]
    
    valid_threshold_crossing_indices = []
    
    # if no saccades were found, then we'll just go on and record an empty saccade
    if threshold_crossing_indices.shape[0] > 1:
        # the first saccade cannot already have started now
        if threshold_crossings_int[threshold_crossing_indices[0]] == -1:
            threshold_crossings_int[threshold_crossing_indices[0]] = 0
            threshold_crossing_indices = threshold_crossing_indices[1:]
    
        # the last saccade cannot be in flight at the end of this data
        if threshold_crossings_int[threshold_crossing_indices[-1]] == 1:
            threshold_crossings_int[threshold_crossing_indices[-1]] = 0
            threshold_crossing_indices = threshold_crossing_indices[:-1]
        
#        if threshold_crossing_indices.shape == 0:
#            break
        # check the durations of the saccades
        threshold_crossing_indices_2x2 = threshold_crossing_indices.reshape((-1,2))
        raw_saccade_durations = np.diff(threshold_crossing_indices_2x2, axis = 1).squeeze()
    
        # and check whether these saccades were also blinks...
        blinks_during_saccades = np.ones(threshold_crossing_indices_2x2.shape[0], dtype = bool)
        for i in range(blinks_during_saccades.shape[0]):
            if np.sum(xy_data_zeros[threshold_crossing_indices_2x2[i,0]-20:threshold_crossing_indices_2x2[i,1]+20]) > 0:
                blinks_during_saccades[i] = False
    
        # and are they too close to the end of the interval?
        right_times = threshold_crossing_indices_2x2[:,1] < xy_data.shape[0]-30
    
        valid_saccades_bool = ((raw_saccade_durations / float(sample_rate) > minimum_saccade_duration) * blinks_during_saccades ) * right_times
        if type(valid_saccades_bool) != np.ndarray:
            valid_threshold_crossing_indices = threshold_crossing_indices_2x2
        else:
            valid_threshold_crossing_indices = threshold_crossing_indices_2x2[valid_saccades_bool]
    
        # print threshold_crossing_indices_2x2, valid_threshold_crossing_indices, blinks_during_saccades, ((raw_saccade_durations / sample_rate) > minimum_saccade_duration), right_times, valid_saccades_bool
        # print raw_saccade_durations, sample_rate, minimum_saccade_duration        
    
    saccades = []
    for i, cis in enumerate(valid_threshold_crossing_indices):
        # find the real start and end of the saccade by looking at when the acceleleration reverses sign before the start and after the end of the saccade:
        # sometimes the saccade has already started?
        expanded_saccade_start = np.arange(cis[0])[np.r_[0,np.diff(signed_acc_data[:cis[0]] != 1)] != 0]
        if expanded_saccade_start.shape[0] > 0:
            expanded_saccade_start = expanded_saccade_start[-1]
        else:
            expanded_saccade_start = 0
            
        expanded_saccade_end = np.arange(cis[1],np.min([cis[1]+50, xy_data.shape[0]]))[np.r_[0,np.diff(signed_acc_data[cis[1]:np.min([cis[1]+50, xy_data.shape[0]])] != -1)] != 0]
        # sometimes the deceleration continues crazily, we'll just have to cut it off then. 
        if expanded_saccade_end.shape[0] > 0:
            expanded_saccade_end = expanded_saccade_end[0]
        else:
            expanded_saccade_end = np.min([cis[1]+50, xy_data.shape[0]])
        
        try:
            this_saccade = {
                'expanded_start_time': expanded_saccade_start,
                'expanded_end_time': expanded_saccade_end,
                'expanded_duration': expanded_saccade_end - expanded_saccade_start,
                'expanded_start_point': xy_data[expanded_saccade_start],
                'expanded_end_point': xy_data[expanded_saccade_end],
                'expanded_vector': xy_data[expanded_saccade_end] - xy_data[expanded_saccade_start],
                'expanded_amplitude': np.sum(normed_vel_data[expanded_saccade_start:expanded_saccade_end]) / sample_rate,
                'peak_velocity': np.max(normed_vel_data[expanded_saccade_start:expanded_saccade_end]),

                'raw_start_time': cis[0],
                'raw_end_time': cis[1],
                'raw_duration': cis[1] - cis[0],
                'raw_start_point': xy_data[cis[1]],
                'raw_end_point': xy_data[cis[0]],
                'raw_vector': xy_data[cis[1]] - xy_data[cis[0]],
                'raw_amplitude': np.sum(normed_vel_data[cis[0]:cis[1]]) / sample_rate,
            }
            saccades.append(this_saccade)
        except IndexError:
            pass
        
    
    # if this fucker was empty
    if len(valid_threshold_crossing_indices) == 0:
        this_saccade = {
            'expanded_start_time': 0,
            'expanded_end_time': 0,
            'expanded_duration': 0.0,
            'expanded_start_point': [0.0,0.0],
            'expanded_end_point': [0.0,0.0],
            'expanded_vector': [0.0,0.0],
            'expanded_amplitude': 0.0,
            'peak_velocity': 0.0,

            'raw_start_time': 0,
            'raw_end_time': 0,
            'raw_duration': 0.0,
            'raw_start_point': [0.0,0.0],
            'raw_end_point': [0.0,0.0],
            'raw_vector': [0.0,0.0],
            'raw_amplitude': 0.0,
        }
        saccades.append(this_saccade)

    # shell()
    return saccades

class EyeSignalOperator(Operator):
    """
    EyeSignalOperator operates on eye signals, preferably sampled at 1000 Hz. 
    This operator is just created by feeding it timepoints,
    eye signals and pupil size signals in separate arrays, on a per-eye basis.
    
    Upon init it creates internal variables self.timepoints, self.raw_gazeXY, self.raw_pupil, self.sample_rate
    and, if available, self.blink_dur, self.blink_starts and self.blink_ends
    
    Its further methods create internal variables storing more derived
    signals that result from further processing.
    """
    def __init__(self, inputObject, **kwargs):
        """inputObject is a dictionary with timepoints, gaze_X, and gaze_Y and pupil keys and timeseries as values"""
        super(EyeSignalOperator, self).__init__(inputObject = inputObject, **kwargs)
        self.timepoints = np.array(self.inputObject['timepoints']).squeeze()
        self.raw_gaze_X = np.array(self.inputObject['gaze_X']).squeeze()
        self.raw_gaze_Y = np.array(self.inputObject['gaze_Y']).squeeze()
        self.raw_pupil = np.array(self.inputObject['pupil']).squeeze()
        
        if hasattr(self, 'eyelink_blink_data'):
            self.blink_dur_EL = np.array(self.eyelink_blink_data['duration']) 
            self.blink_starts_EL = np.array(self.eyelink_blink_data['start_timestamp'])[self.blink_dur_EL<4000] - self.timepoints[0]
            self.blink_ends_EL = np.array(self.eyelink_blink_data['end_timestamp'])[self.blink_dur_EL<4000] - self.timepoints[0]
        
        if hasattr(self, 'eyelink_sac_data'):
            self.sac_dur_EL = np.array(self.eyelink_sac_data['duration']) 
            self.sac_starts_EL = np.array(self.eyelink_sac_data['start_timestamp']) - self.timepoints[0]
            self.sac_ends_EL = np.array(self.eyelink_sac_data['end_timestamp']) - self.timepoints[0]
        
        if not hasattr(self, 'sample_rate'): # this should have been set as a kwarg, but if it hasn't we just assume a standard 1000 Hz
            self.sample_rate = 1000.0

    
    def interpolate_blinks(self, method='linear', lin_interpolation_points=[[-250],[250]], spline_interpolation_points=[[-0.15,-0.075], [0.075,0.15]], coalesce_period=750):
        """
        interpolate_blinks interpolates blink periods with method, which can be spline or linear.
        Use after self.blink_detection_pupil().
        spline_interpolation_points is a 2 by X list detailing the data points around the blinks
        (in s offset from blink start and end) that should be used for fitting the interpolation spline.

        The results are stored in self.interpolated_pupil, self.interpolated_x and self.interpolated_y
        without affecting the self.raw_... variables

        After calling this method, additional interpolation may be performed by calling self.interpolate_blinks2()
        """
        self.logger.info('Interpolating blinks using interpolate_blinks')
        # set all missing data to 0:
        self.raw_pupil[self.raw_pupil<1] = 0
        
        # blinks to work with -- preferably eyelink!
        if hasattr(self, 'eyelink_blink_data'):			
            for i in range(len(self.blink_starts_EL)):
                self.raw_pupil[int(self.blink_starts_EL[i]):int(self.blink_ends_EL[i])] = 0 # set all eyelink-identified blinks to 0:
        # else:
        #     self.blinks_indices = pd.rolling_mean(np.array(self.raw_pupil < threshold_level, dtype = float), int(coalesce_period)) > 0
        #     self.blinks_indices = np.array(self.blinks_indices, dtype=int)
        #     self.blink_starts = self.timepoints[:-1][np.diff(self.blinks_indices) == 1]
        #     self.blink_ends = self.timepoints[:-1][np.diff(self.blinks_indices) == -1]
        #     # now make sure we're only looking at the blnks that fall fully inside the data stream
        #     try:
        #         if self.blink_starts[0] > self.blink_ends[0]:
        #             self.blink_ends = self.blink_ends[1:]
        #         if self.blink_starts[-1] > self.blink_ends[-1]:
        #             self.blink_starts = self.blink_starts[:-1]
        #     except:
        #         shell()
        
        # we do not want to start or end with a 0:
        import copy
        self.interpolated_pupil = copy.copy(self.raw_pupil[:])
        self.interpolated_x = copy.copy(self.raw_gaze_X)
        self.interpolated_y = copy.copy(self.raw_gaze_Y)
        self.interpolated_pupil[:coalesce_period] = np.mean(self.interpolated_pupil[np.where(self.interpolated_pupil > 0)[0][:1000]])
        self.interpolated_pupil[-coalesce_period:] = np.mean(self.interpolated_pupil[np.where(self.interpolated_pupil > 0)[0][-1000:]])
        self.interpolated_x[:coalesce_period] = np.mean(self.interpolated_x[np.where(self.interpolated_pupil > 0)[0][:1000]])
        self.interpolated_x[-coalesce_period:] = np.mean(self.interpolated_x[np.where(self.interpolated_pupil > 0)[0][-1000:]])
        self.interpolated_y[:coalesce_period] = np.mean(self.interpolated_y[np.where(self.interpolated_pupil > 0)[0][:1000]])
        self.interpolated_y[-coalesce_period:] = np.mean(self.interpolated_y[np.where(self.interpolated_pupil > 0)[0][-1000:]])
        
        # detect zero edges (we just created from blinks, plus missing data):
        zero_edges = np.arange(self.interpolated_pupil.shape[0])[:-1][np.diff((self.interpolated_pupil<1))]
        if zero_edges.shape[0] == 0:
            pass
        else:
            zero_edges = zero_edges[:int(2 * np.floor(zero_edges.shape[0]/2.0))].reshape(-1,2)
        
        try:
            self.blink_starts = zero_edges[:,0]
            self.blink_ends = zero_edges[:,1]
        except: # in case there are no blinks!
            self.blink_starts = np.array([coalesce_period/2.0])
            self.blink_ends = np.array([(coalesce_period/2.0)+10])
        
        # check for neighbouring blinks (coalesce_period, default is 500ms), and string them together:
        start_indices = np.ones(self.blink_starts.shape[0], dtype=bool)
        end_indices = np.ones(self.blink_ends.shape[0], dtype=bool)
        for i in range(self.blink_starts.shape[0]):
            try:
                if self.blink_starts[i+1] - self.blink_ends[i] <= coalesce_period:
                    start_indices[i+1] = False
                    end_indices[i] = False
            except IndexError:
                pass
        
        # these are the blink start and end samples to work with:
        if sum(start_indices) > 0:
            self.blink_starts = self.blink_starts[start_indices]
            self.blink_ends = self.blink_ends[end_indices]
        else:
            self.blink_starts = None
            self.blink_ends = None
        
        # do actual interpolation:
        if method == 'spline':
            points_for_interpolation = np.array(np.array(spline_interpolation_points) * self.sample_rate, dtype = int)
            for bs, be in zip(self.blink_starts, self.blink_ends):
                samples = np.ravel(np.array([bs + points_for_interpolation[0], be + points_for_interpolation[1]]))
                sample_indices = np.arange(self.raw_pupil.shape[0])[np.sum(np.array([self.timepoints == s for s in samples]), axis = 0)]
                spline = interpolate.InterpolatedUnivariateSpline(sample_indices,self.raw_pupil[sample_indices])
                self.interpolated_pupil[sample_indices[0]:sample_indices[-1]] = spline(np.arange(sample_indices[1],sample_indices[-2]))
                spline = interpolate.InterpolatedUnivariateSpline(sample_indices,self.raw_gaze_X[sample_indices])
                self.interpolated_x[sample_indices[0]:sample_indices[-1]] = spline(np.arange(sample_indices[1],sample_indices[-2]))
                spline = interpolate.InterpolatedUnivariateSpline(sample_indices,self.raw_gaze_Y[sample_indices])
                self.interpolated_y[sample_indices[0]:sample_indices[-1]] = spline(np.arange(sample_indices[1],sample_indices[-2]))
        elif method == 'linear':
            if sum(start_indices) > 0:
                points_for_interpolation = np.array([self.blink_starts, self.blink_ends], dtype=int).T + np.array(lin_interpolation_points).T
                for itp in points_for_interpolation:
                    self.interpolated_pupil[itp[0]:itp[-1]] = np.linspace(self.interpolated_pupil[itp[0]], self.interpolated_pupil[itp[-1]], itp[-1]-itp[0])
                    self.interpolated_x[itp[0]:itp[-1]] = np.linspace(self.interpolated_x[itp[0]], self.interpolated_x[itp[-1]], itp[-1]-itp[0])
                    self.interpolated_y[itp[0]:itp[-1]] = np.linspace(self.interpolated_y[itp[0]], self.interpolated_y[itp[-1]], itp[-1]-itp[0])
    
    def interpolate_blinks2(self, lin_interpolation_points = [[-250],[250]], coalesce_period=750):
        
        """
        interpolate_blinks2 performs linear interpolation around peaks in the rate of change of
        the pupil size.
        
        The results are stored in self.interpolated_pupil, self.interpolated_x and self.interpolated_y
        without affecting the self.raw_... variables.
        
        This method is typically called after an initial interpolation using self.interpolateblinks(),
        consistent with the fact that this method expects the self.interpolated_... variables to already exist.
        """
        
        from Tools.other_scripts import functions_jw as myfuncs
        
        # self.pupil_diff = (np.diff(self.interpolated_pupil) - np.diff(self.interpolated_pupil).mean()) / np.diff(self.interpolated_pupil).std()
        # self.peaks = myfuncs.detect_peaks(self.pupil_diff, mph=10, mpd=500, threshold=None, edge='rising', kpsh=False, valley=False, show=False, ax=False)[:-1] # last peak might not reflect blink...
        # if self.peaks != None:
        #     points_for_interpolation = np.array([self.peaks, self.peaks], dtype=int).T + np.array(lin_interpolation_points).T
        #     for itp in points_for_interpolation:
        #         self.interpolated_pupil[itp[0]:itp[-1]] = np.linspace(self.interpolated_pupil[itp[0]], self.interpolated_pupil[itp[-1]], itp[-1]-itp[0])
        #         self.interpolated_x[itp[0]:itp[-1]] = np.linspace(self.interpolated_x[itp[0]], self.interpolated_x[itp[-1]], itp[-1]-itp[0])
        #         self.interpolated_y[itp[0]:itp[-1]] = np.linspace(self.interpolated_y[itp[0]], self.interpolated_y[itp[-1]], itp[-1]-itp[0])
        
        self.interpolated_time_points = np.zeros(len(self.interpolated_pupil))
        self.pupil_diff = (np.diff(self.interpolated_pupil) - np.diff(self.interpolated_pupil).mean()) / np.diff(self.interpolated_pupil).std()
        peaks_down = myfuncs.detect_peaks(self.pupil_diff, mph=10, mpd=1, threshold=None, edge='rising', kpsh=False, valley=False, show=False, ax=False)
        peaks_up = myfuncs.detect_peaks(self.pupil_diff*-1, mph=10, mpd=1, threshold=None, edge='rising', kpsh=False, valley=False, show=False, ax=False)
        self.peaks = np.sort(np.concatenate((peaks_down, peaks_up)))
        
        if len(self.peaks) > 0:
            
            # prepare:
            self.peak_starts = np.sort(np.concatenate((self.peaks-1, self.blink_starts)))
            self.peak_ends = np.sort(np.concatenate((self.peaks+1, self.blink_ends)))
            start_indices = np.ones(self.peak_starts.shape[0], dtype=bool)
            end_indices = np.ones(self.peak_ends.shape[0], dtype=bool)
            for i in range(self.peak_starts.shape[0]):
                try:
                    if self.peak_starts[i+1] - self.peak_ends[i] <= coalesce_period:
                        start_indices[i+1] = False
                        end_indices[i] = False
                except IndexError:
                    pass
            self.peak_starts = self.peak_starts[start_indices]
            self.peak_ends = self.peak_ends[end_indices] 
            
            # interpolate:
            points_for_interpolation = np.array([self.peak_starts, self.peak_ends], dtype=int).T + np.array(lin_interpolation_points).T
            for itp in points_for_interpolation:
                self.interpolated_pupil[itp[0]:itp[-1]] = np.linspace(self.interpolated_pupil[itp[0]], self.interpolated_pupil[itp[-1]], itp[-1]-itp[0])
                self.interpolated_x[itp[0]:itp[-1]] = np.linspace(self.interpolated_x[itp[0]], self.interpolated_x[itp[-1]], itp[-1]-itp[0])
                self.interpolated_y[itp[0]:itp[-1]] = np.linspace(self.interpolated_y[itp[0]], self.interpolated_y[itp[-1]], itp[-1]-itp[0])
                self.interpolated_time_points[itp[0]:itp[-1]] = 1
                     
    def filter_pupil(self, hp = 0.01, lp = 10.0):
        """
        band_pass_filter_pupil band pass filters the pupil signal using a butterworth filter of order 3. 
        
        The results are stored in self.lp_filt_pupil, self.hp_filt_pupil and self.bp_filt_pupil
        
        This method is typically called after self.interpolateblinks() and, optionally, self.interpolateblinks2(),
        consistent with the fact that this method expects the self.interpolated_... variables to exist.
        """
        self.logger.info('Band-pass filtering of pupil signals, hp = %2.3f, lp = %2.3f'%(hp, lp))
        
        self.lp_filt_pupil = _butter_lowpass(data=self.interpolated_pupil.astype('float64'), highcut=lp, fs=self.sample_rate, order=3)
        self.hp_filt_pupil = _butter_highpass(data=self.interpolated_pupil.astype('float64'), lowcut=hp, fs=self.sample_rate, order=3)
        self.bp_filt_pupil = self.hp_filt_pupil - (self.interpolated_pupil-self.lp_filt_pupil)
        self.baseline_filt_pupil = self.lp_filt_pupil - self.bp_filt_pupil
        
        # import mne
        # from mne import filter
        # self.lp_filt_pupil = mne.filter.low_pass_filter(x=self.interpolated_pupil.astype('float64'), Fs=self.sample_rate, Fp=lp, filter_length=None, method='iir', iir_params={'ftype':'butter', 'order':3}, picks=None, n_jobs=1, copy=True, verbose=None)
        # self.hp_filt_pupil = mne.filter.high_pass_filter(x=self.interpolated_pupil.astype('float64'), Fs=self.sample_rate, Fp=hp, filter_length=None, method='iir', iir_params={'ftype':'butter', 'order':3}, picks=None, n_jobs=1, copy=True, verbose=None)
        # self.bp_filt_pupil = self.hp_filt_pupil - (self.interpolated_pupil-self.lp_filt_pupil)
        # self.baseline_filt_pupil = self.lp_filt_pupil - self.bp_filt_pupil
                
    def zscore_pupil(self, dtype = 'bp_filt_pupil'):
        """
        zscore_pupil takes z-score of the dtype pupil signal, and internalizes it as a dtype + '_zscore' self variable.
        """
        
        exec('self.' + str(dtype) + '_zscore = (self.' + str(dtype) + ' - np.mean(self.' + str(dtype) + ')) / np.std(self.' + str(dtype) + ')')
        
    def percent_signal_change_pupil(self, dtype = 'bp_filt_pupil'):
        """
        percent_signal_change_pupil takes percent signal change of the dtype pupil signal, and internalizes it as a dtype + '_psc' self variable.
        """
        
        exec('self.{}_psc = ((self.{} - self.{}.mean()) / np.mean(self.baseline_filt_pupil[500:-500])) * 100'.format(dtype, dtype, dtype))
        
    def dt_pupil(self, dtype = 'bp_filt_pupil'):
        """
        dt_pupil takes the temporal derivative of the dtype pupil signal, and internalizes it as a dtype + '_dt' self variable.
        """
        
        exec('self.' + str(dtype) + '_dt = np.r_[0, np.diff(self.' + str(dtype) + ')]' )

    def time_frequency_decomposition_pupil(self, 
                                           minimal_frequency = 0.0025, 
                                           maximal_frequency = 0.1, 
                                           nr_freq_bins = 7, 
                                           n_cycles = 1, 
                                           cycle_buffer = 3, 
                                           tf_decomposition='lp_butterworth'): 
        """time_frequency_decomposition_pupil has two options of time frequency decomposition on the pupil  data: 1) morlet wavelet transform from mne package 
            or 2) low-pass butterworth filters. Before tf-decomposition the minimal frequency in the data is compared to the input minimal_frequency using np.fft.fftfreq. 
            
            1) Morlet wavelet transform. Interpolated pupil data is z-scored and zero-padded to avoid edge artifacts during wavelet transformation. After morlet 
            transform, zero-padding is removed and transformed data is saved in a DataFrame self.band_pass_filter_bank_pupil with wavelet frequencies as columns.
            
            2) Low-pass butterworth filters. Low-pass cutoff samples are calculated for each frequency in frequencies. Low-pass filtering is performed and saved in 
            lp_filter_bank_pupil. Note: low-pass filtered signals are not yet band-pass here, thus, filtered signals with higher frequency cutoffs share lower frequency 
            information at that point. band_pass_signals calculates the difference between subsequent lp_filter_bank_pupil signals to make independent filter bands and 
            vstacks the lowest frequency to the datamatrix. Lastly, band_pass_signals are saved in a df in self.band_pass_filter_bank_pupil with low-pass frequencies as columns. 
            """
        
        # check minimal frequency
        min_freq_in_data = np.fft.fftfreq(self.timepoints.shape[0], 1.0/self.sample_rate)[1] 
        if minimal_frequency < min_freq_in_data and minimal_frequency != None:
            self.logger.warning("""time_frequency_decomposition_pupil: 
                                    requested minimal_frequency %2.5f smaller than 
                                    data allows (%2.5f). """%(minimal_frequency, min_freq_in_data))

        if minimal_frequency == None:
            minimal_frequency = min_freq_in_data

        # use minimal_frequency for bank of logarithmically frequency-spaced filters
        frequencies = np.logspace(np.log10(maximal_frequency), np.log10(minimal_frequency), nr_freq_bins)
        self.logger.info('Time_frequency_decomposition_pupil, with filterbank %s'%str(frequencies))
    
        if tf_decomposition == 'morlet': 
            #z-score self.interpolated_pupil before morlet decomposition of pupil signal 
            interpolated_pupil_z = ((self.interpolated_pupil - np.mean(self.interpolated_pupil))/self.interpolated_pupil.std())
            #zero-pad runs to avoid edge-artifacts 
            zero_padding_samples = int((1/minimal_frequency)*self.sample_rate*cycle_buffer)
            padded_interpolated_pupil_z = np.zeros((interpolated_pupil_z.shape[0] + 2*(zero_padding_samples)))
            padded_interpolated_pupil_z[zero_padding_samples:-zero_padding_samples] = interpolated_pupil_z    
            #filtered signal is real part of Morlet-transformed signal
            padded_band_pass_filter_bank_pupil = np.squeeze(np.real(mne.time_frequency.cwt_morlet(padded_interpolated_pupil_z[np.newaxis,:], self.sample_rate, frequencies, use_fft=True, n_cycles=n_cycles, zero_mean=True)))
            #remove zero-padding and save as dataframe with frequencies as index
            self.band_pass_filter_bank_pupil = pd.DataFrame(np.array([padded_band_pass_filter_bank_pupil[i][zero_padding_samples:-zero_padding_samples] for i in range(len(padded_band_pass_filter_bank_pupil))]).T,columns=frequencies)
        
        elif tf_decomposition == 'lp_butterworth': 
            lp_filter_bank_pupil=np.zeros((len(frequencies), self.interpolated_pupil.shape[0]))
            lp_cof_samples = [freq / (self.interpolated_pupil.shape[0] / self.sample_rate / 2) for freq in frequencies]
            for i, lp_cutoff in enumerate(lp_cof_samples): 
                blp, alp = sp.signal.butter(3, lp_cutoff) 
                lp_filt_pupil = sp.signal.filtfilt(blp, alp, self.interpolated_pupil)
                lp_filter_bank_pupil[i,0:self.interpolated_pupil.shape[0]]=lp_filt_pupil
            #calculate band passes from the difference between subsequent low pass frequencies (except the last frequency, this one is directly added to the df as the lowest freq in data) 
            band_pass_signals = np.vstack((np.array(lp_filter_bank_pupil[:-1]) - np.array(lp_filter_bank_pupil[1:]), lp_filter_bank_pupil[-1]))
            self.band_pass_filter_bank_pupil = pd.DataFrame(band_pass_signals.T, columns=frequencies)
        
        else: 
            print('you did not specify a tf-decomposition')
    
    def regress_blinks(self,):
        
        # params:
        self.downsample_rate = 100
        self.new_sample_rate = self.sample_rate / self.downsample_rate
        interval = 5
        
        # events:
        blinks = self.blink_ends / self.sample_rate
        blinks = blinks[blinks>25]
        blinks = blinks[blinks<((self.timepoints[-1]-self.timepoints[0])/self.sample_rate)-interval]
        
        if blinks.size == 0:
            blinks = np.array([0.5])
        
        sacs = self.sac_ends_EL / self.sample_rate
        sacs = sacs[sacs>25]
        sacs = sacs[sacs<((self.timepoints[-1]-self.timepoints[0])/self.sample_rate)-interval]
        events = [blinks, sacs]
        
        # # compute blink and sac kernels with deconvolution (on downsampled timeseries):
        # a = fir.FIRDeconvolution(signal=sp.signal.decimate(self.bp_filt_pupil, self.downsample_rate, 1), events=events, event_names=['blinks', 'sacs'], sample_frequency=self.new_sample_rate, deconvolution_frequency=self.new_sample_rate, deconvolution_interval=[0,interval],)
        # a.create_design_matrix()
        # a.regress()
        # a.betas_for_events()
        # self.blink_response = np.array(a.betas_per_event_type[0]).ravel()
        # self.sac_response = np.array(a.betas_per_event_type[1]).ravel()
        
        # compute blink and sac kernels with deconvolution (on downsampled timeseries):
        do = ArrayOperator.DeconvolutionOperator( inputObject=sp.signal.decimate(self.bp_filt_pupil, self.downsample_rate, 1), eventObject=events, TR=(1.0 / self.new_sample_rate), deconvolutionSampleDuration=(1.0 / self.new_sample_rate), deconvolutionInterval=interval, run=True )
        self.blink_response = np.array(do.deconvolvedTimeCoursesPerEventType[0]).ravel()
        self.sac_response = np.array(do.deconvolvedTimeCoursesPerEventType[1]).ravel()
        
        # demean:
        self.blink_response = self.blink_response - self.blink_response[:int(0.2*self.new_sample_rate)].mean()
        self.sac_response = self.sac_response - self.sac_response[:int(0.2*self.new_sample_rate)].mean()
        
        # fit:
        # ----
        
        # define objective function: returns the array to be minimized
        def double_gamma_ls(params, x, data): 
            
            a1 = params['a1'].value
            sh1 = params['sh1'].value
            sc1 = params['sc1'].value
            a2 = params['a2'].value
            sh2 = params['sh2'].value
            sc2 = params['sc2'].value
            
            model = a1 * sp.stats.gamma.pdf(x, sh1, loc=0.0, scale = sc1) + a2 * sp.stats.gamma.pdf(x, sh2, loc=0.0, scale = sc2)
            
            return model - data
        
        def double_gamma(params, x): 
            a1 = params['a1']
            sh1 = params['sh1']
            sc1 = params['sc1']
            a2 = params['a2']
            sh2 = params['sh2']
            sc2 = params['sc2']
            return a1 * sp.stats.gamma.pdf(x, sh1, loc=0.0, scale = sc1) + a2 * sp.stats.gamma.pdf(x, sh2, loc=0.0, scale = sc2)
        def pupil_IRF(params, x):
            s1 = params['s1']
            n1 = params['n1']
            tmax1 = params['tmax1']
            
            return s1 * (x**n1) * (np.e**((-n1*x)/tmax1))
        def double_pupil_IRF(params, x):
            s1 = params['s1']
            s2 = params['s2']
            n1 = params['n1']
            n2 = params['n2']
            tmax1 = params['tmax1']
            tmax2 = params['tmax2']
            
            return s1 * ((x**n1) * (np.e**((-n1*x)/tmax1))) + s2 * ((x**n2) * (np.e**((-n2*x)/tmax2)))
            
            
        def double_pupil_IRF_ls(params, x, data):
            s1 = params['s1'].value
            s2 = params['s2'].value
            n1 = params['n1'].value
            n2 = params['n2'].value
            tmax1 = params['tmax1'].value
            tmax2 = params['tmax2'].value
            
            model = s1 * ((x**n1) * (np.e**((-n1*x)/tmax1))) + s2 * ((x**n2) * (np.e**((-n2*x)/tmax2)))
            
            return model - data
            
        # create data to be fitted
        x = np.linspace(0,interval,len(self.blink_response))
        
        # # create a set of Parameters
        # params = Parameters()
        # params.add('a1', value=-1, min=-np.inf, max=-1e-25)
        # params.add('a2', value=0.4, min=1e-25, max=np.inf)
        # params.add('sh1', value=8, min=4, max=10)
        # params.add('sh2', value=15,) #min=10, max=20)
        # params.add('sc1', value=0.1, min=0, max=1)
        # params.add('sc2', value=0.2, min=0, max=3)
        #
        # # do fit, here with leastsq model
        # data = self.blink_response
        # blink_result = minimize(double_gamma_ls, params, args=(x, data))
        # self.blink_fit = double_gamma(blink_result.values, x)
        #
        # data = self.sac_response
        # sac_result = minimize(double_gamma_ls, params, args=(x, data))
        # self.sac_fit = double_gamma(sac_result.values, x)
        #
        # # upsample:
        # x = np.linspace(0,interval,interval*self.sample_rate)
        # blink_kernel = double_gamma(blink_result.values, x)
        # sac_kernel = double_gamma(sac_result.values, x)
        
        # create a set of Parameters
        params = Parameters()
        params.add('s1', value=-1, min=-np.inf, max=-1e-25)
        params.add('s2', value=1, min=1e-25, max=np.inf)
        params.add('n1', value=10, min=9, max=11)
        params.add('n2', value=10, min=8, max=12)
        params.add('tmax1', value=0.9, min=0.5, max=1.5)
        params.add('tmax2', value=2.5, min=1.5, max=4)

        # do fit, here with powell method:
        data = self.blink_response
        blink_result = minimize(double_pupil_IRF_ls, params, method='powell', args=(x, data))
        self.blink_fit = double_pupil_IRF(blink_result.params, x)
        data = self.sac_response
        sac_result = minimize(double_pupil_IRF_ls, params, method='powell', args=(x, data))
        self.sac_fit = double_pupil_IRF(sac_result.params, x)
        
        # upsample:
        x = np.linspace(0,interval,interval*self.sample_rate)
        blink_kernel = double_pupil_IRF(blink_result.params, x)
        sac_kernel = double_pupil_IRF(sac_result.params, x)
        
        # use standard values:
        # standard_values = {'a1':-0.604, 'sh1':8.337, 'sc1':0.115, 'a2':0.419, 'sh2':15.433, 'sc2':0.178}
        # blink_kernel = double_gamma(standard_values, x)
        # sac_kernel = double_gamma(standard_values, x)
        
        # regress out from original timeseries with GLM:
        event_1 = np.ones((len(blinks),3))
        event_1[:,0] = blinks
        event_1[:,1] = 0
        event_2 = np.ones((len(sacs),3))
        event_2[:,0] = sacs
        event_2[:,1] = 0
        GLM = functions_jw_GLM.GeneralLinearModel(input_object=self.bp_filt_pupil, event_object=[event_1, event_2], sample_dur=1.0/self.sample_rate, new_sample_dur=1.0/self.sample_rate)
        GLM.configure(IRF=[blink_kernel, sac_kernel], regressor_types=['stick', 'stick'],)
        GLM.design_matrix = np.vstack((GLM.design_matrix[0], GLM.design_matrix[3]))
        GLM.execute()
        
        self.GLM_measured = GLM.working_data_array
        self.GLM_predicted = GLM.predicted
        self.GLM_r, self.GLM_p = sp.stats.pearsonr(self.GLM_measured, self.GLM_predicted)
        
        # clean data:
        self.bp_filt_pupil_clean = GLM.residuals
        
        # final timeseries:
        self.lp_filt_pupil_clean = self.bp_filt_pupil_clean + self.baseline_filt_pupil
        self.bp_filt_pupil_clean = self.bp_filt_pupil_clean + self.baseline_filt_pupil.mean()
        
    def summary_plot(self):
        
        import matplotlib.gridspec as gridspec
        
        fig = plt.figure(figsize=(6,10))
        gs = gridspec.GridSpec(5, 4)
        ax1 = plt.subplot(gs[0,:])
        ax2 = plt.subplot(gs[1,:])
        ax3 = plt.subplot(gs[2,0:2])
        ax4 = plt.subplot(gs[2,2:4])
        ax5 = plt.subplot(gs[3,:])
        ax6 = plt.subplot(gs[4,:])
        
        x = np.linspace(0,self.raw_pupil.shape[0]/self.sample_rate, self.raw_pupil.shape[0])
        ax1.plot(x, self.raw_pupil, 'b', rasterized=True)
        ax1.plot(x, self.interpolated_pupil, 'g', rasterized=True)
        ax1.set_title('Raw and blink interpolated timeseries')
        ax1.set_ylabel('Pupil size (raw)')
        ax1.set_xlabel('Time (s)')
        ax1.legend(['Raw', 'Int + filt'])
        
        ax2.plot(self.pupil_diff, rasterized=True)
        ax2.plot(self.peaks, self.pupil_diff[self.peaks], '+', mec='r', mew=2, ms=8, rasterized=True)
        ax2.set_ylim(ymin=-200, ymax=200)
        ax2.set_title('Remaining blinks?')
        ax2.set_ylabel('Diff pupil size (raw)')
        ax2.set_xlabel('Samples')
        
        try:
            x = np.linspace(0,self.blink_response.shape[0]/self.new_sample_rate, self.blink_response.shape[0])
            ax3.plot(x, self.blink_response, label='response')
            ax3.plot(x, self.blink_fit, label='fit')
            ax3.legend()
            ax3.set_title('Blink response')
            ax3.set_xlabel('Time (s)')
            ax3.set_ylabel('Pupil size (raw)')
            
            ax4.plot(x, self.sac_response, label='response')
            ax4.plot(x, self.sac_fit, label='fit')
            ax4.legend()
            ax4.set_title('Saccade response')
            ax4.set_xlabel('Time (s)')
            ax4.set_ylabel('Pupil size (raw)')

            x = np.linspace(0,self.raw_pupil.shape[0]/self.sample_rate, self.raw_pupil.shape[0])
            ax5.plot(x, self.GLM_measured, 'b', rasterized=True)
            ax5.plot(x, self.GLM_predicted, lw=2, color='g', rasterized=True)
            ax5.set_title('Nuisance GLM -- R2={}, p={}'.format(round(self.GLM_r,4), round(self.GLM_p,4)))
            ax5.set_ylabel('Pupil size (raw)')
            ax5.set_xlabel('Time (s)')
            ax5.legend(['measured', 'predicted'])
        except:
            pass
        
        ax6.plot(x, self.lp_filt_pupil_psc, 'b', rasterized=True)
        ax6.plot(x, self.lp_filt_pupil_clean_psc, 'g', rasterized=True)
        ax6.set_title('Final timeseries')
        ax6.set_ylabel('Pupil size (% signal change)')
        ax6.set_xlabel('Time (s)')
        ax6.legend(['low pass', 'low pass + cleaned up'])
        
        plt.tight_layout()
        
        return fig
        