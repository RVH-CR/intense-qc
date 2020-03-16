"""
INTENSE QC Component 2 - Quality Control

This component of the INTENSE QC package reads rainfall data formatted as an INTENSE
Series and executes the flagging process by which the data is checked. 
NO DATA IS EXCLUDED IN THIS CODE!
To exclude flagged data use component 3: Rulebase. 

This QC code will use the INTENSE module to create QC object

Data is read in by the INTENSE module as 
    data = pd.Series(data, 
                     pd.date_range(start=pd.datetime.strptime(metadata['start datetime'],'%Y%m%d%H'),
                     end=pd.datetime.strptime(metadata['end datetime'],'%Y%m%d%H'),
                     freq=metadata['new timestep'][:-2]+'H'),
                     dtype=float)
    
    
The INTENSE object looks like this:
    s =  Series(station_id=metadata['station id'],
                path_to_original_data=metadata['path to original data'],
                latitude=tryFloat(metadata['latitude']),
                longitude=tryFloat(metadata['longitude']),
                original_timestep=metadata['original timestep'],
                original_units=metadata['original units'],
                new_units=metadata['new units'],
                new_timestep=metadata['new timestep'],
                elevation=metadata['elevation'],
                data=data)
    
For more details on INTENSE objects and the associated functionality refer to 
Component 1: intense_CW.py

Required packages: 
    intense
    pandas
    numpy
    rpy2
    xarray
    scipy
    datetime
    zipfile
    subprocess
    os
    
Developed by: 
    Elizabeth Lewis, PhD
    SB, RV, others...

Publication to be cited:
    Paper

June 2019 
"""
    
##import intense.intense as ex
import intense as ex
import pandas as pd
import numpy as np
import rpy2.robjects as robjects
from rpy2.robjects.packages import importr
import xarray as xr
import numpy.ma as ma
import scipy.interpolate
import datetime
import os
import zipfile
import pickle
import math
import scipy.spatial as sp
import subprocess
#from scipy.stats import ttest_1samp as tt
import scipy.stats
import matplotlib.pyplot as plt
from multiprocessing import Pool, Manager
import sys
import calendar
import time

trend = importr('trend')

"""
+++++++++++++++++++++++++++++++++++++++++++ Basic checks +++++++++++++++++++++++++++++++++++++++++++
"""
# Indicative check to flag years with 95th or 99th percentiles equal to zero.
def checkPercentiles(its): 
    perc95 = its.data.groupby(pd.Grouper(freq='A')).quantile(.95)
    perc99 = its.data.groupby(pd.Grouper(freq='A')).quantile(.99)
    
    return [[d.year for d in list(perc95[perc95 == 0].index)],[d.year for d in list(perc99[perc99 == 0].index)]]

# Indicative check to flag years with K-largest values equal to zero.
def checkKLargest(its): 
    k1 = its.data.groupby(pd.Grouper(freq='A')).nlargest(n=1).min(level=0)  
    k5 = its.data.groupby(pd.Grouper(freq='A')).nlargest(n=5).min(level=0)  
    k10 = its.data.groupby(pd.Grouper(freq='A')).nlargest(n=10).min(level=0)  

    return [[d.year for d in list(k1[k1 == 0].index)], [d.year for d in list(k5[k5 == 0].index)], [d.year for d in list(k10[k10 == 0].index)]]
    
# Indicative, checks if proportions of rainfall in each day is significantly different
def checkDaysOfWeek(its): 
    key = lambda x: x.weekday # 0 is monday, 1 is tuesday etc...
    days = its.data.groupby(key).mean()
    popmean = its.data.mean()
    #p = tt(days, popmean)[1]
    p = scipy.stats.ttest_1samp(days, popmean)[1]
    if p < 0.01: #different
        return 1
    else:
        return 0
    
# Indicative, hourly analogue to daily check
def checkHoursOfDay(its): 
    key = lambda x: x.hour # 0 is midnight, 1 is 01:00 etc...
    hours = its.data.groupby(key).mean()
    popmean = its.data.mean()
    #p = tt(hours, popmean)[1]
    p = scipy.stats.ttest_1samp(hours, popmean)[1]
    if p < 0.01: #different
        return 1
    else:
        return 0

# Annual check for discontinuous records.
# Returns years where more than 5 no data periods are bounded by zeros.
# A no data period is defined as 2 or more consecutive missing values.
# def checkIntermittency(its): 

    # """
    # Return years where more than 5 no data periods are bounded by zeros
    # A no data period is defined as 2 or more consecutive missing values
    # For a year to be flagged no data periods must occur in at least 5 different days
    # """
    # vals = its.data
    # gaps = pd.DataFrame()
    # count = 0
    # for i in range(1,len(vals)-1): # start with second TS and proceed to end
        # if (vals.iloc[i-1] == 0) & (np.isnan(vals.iloc[i])):
            # startTS = vals.index[i]
            # count += 1 #+=1
        # elif (np.isnan(vals.iloc[i])) & (count >= 1):
            # count +=1
        # elif (np.isnan(vals.iloc[i-1])) & (vals.iloc[i] == 0) & (count >= 2):
            # gaps = gaps.append({'datetime':startTS,'Missing_len':count},ignore_index = True)
            # count = 0
        # else:
            # count = 0
    # if len(gaps) > 0 :
        # gaps.index = gaps.datetime # Set datetime as index
        # annCount = gaps.resample('1Y').count() # Get annual DF with count of intermittency instances
        # return [d.year for d in list(annCount[annCount >5].index)]
    # else:
        # return []

# Alternative implementation of intermittency check
def checkIntermittency(its):
    
    # Shift data +/- 1 hour to help identify missing data periods with vectorised approach
    df = its.data.copy().to_frame()
    df.columns = ['val']
    df['prev'] = df.shift(1)['val']
    df['next'] = df.shift(-1)['val']

    # Look for >=2 consecutive missing values (bounds by >=0 values first)
    # - find start and end indices of these periods
    start_inds = np.flatnonzero((np.isfinite(df.prev)) & (np.isnan(df.val)) & 
        (np.isnan(df.next)))
    end_inds = np.flatnonzero((np.isnan(df.prev)) & (np.isnan(df.val)) & 
        (np.isfinite(df.next)))
    
    # If no final non-nan value then if a missing period assign end index as 
    # end of series
    if len(start_inds) == len(end_inds):
        pass
    elif len(start_inds) == len(end_inds) + 1:
        end_inds = end_inds.tolist()
        end_inds.append(len(df['val']) - 1)
        end_inds = np.asarray(end_inds, dtype=np.int)
    else:
        print('intermittency period identification error')
        #sys.exit()
    
    # Select only the periods preceded and followed by dry hours
    start_inds2 = []
    end_inds2 = []
    if len(start_inds) > 0:
        for si,ei in zip(start_inds, end_inds):
            
            # Account for case that first start index is beginning of series and
            # case that final end index is end of series
            if (si == 0) or (ei == (df['val'].shape[0] - 1)):
                start_inds2.append(si)
                end_inds2.append(ei)
            
            # Otherwise check if preceding/following values are dry
            else:
                if (df['prev'][si] == 0) and (df['next'][ei] == 0):
                    start_inds2.append(si)
                    end_inds2.append(ei)
            
    start_inds = start_inds2
    end_inds = end_inds2
    
    # Count missing periods by year
    # - just count year in which the missing period begins, i.e. ignore if finishes
    # in e.g. the next year for now
    if len(start_inds) > 0:
        '''
        dc = {}
        dates = []
        for si,ei in zip(start_inds, end_inds):
            start_year = df.index.year[si]
            end_year = df.index.year[ei]
            d = df.index.date[si]
            if d not in dates:
                if start_year not in dc.keys():
                    dc[start_year] = 1
                else:
                    dc[start_year] += 1
                if start_year != end_year:
                    if end_year not in dc.keys():
                        dc[end_year] = 1
                    else:
                        dc[end_year] += 1
                dates.append(d)

        # Make final list of years with >=5 missing periods
        flag_years = []
        for year,count in dc.items():
            if count >= 5:
                flag_years.append(year)
        flag_years = sorted(flag_years)
        '''
        
        # Alternative faster approach using dataframe operations
        df1 = df.iloc[start_inds].copy()
        df1['date'] = df1.index.date
        df1['year'] = df1.index.year
        df1.drop_duplicates('date', inplace=True)
        df2 = df1.groupby(df1['year'])['year'].agg('count')
        df2 = df2.to_frame()
        df2.columns = ['count']
        df2 = df2.loc[df2['count'] >= 5]
        flag_years = df2.index.tolist()
    
    else:
        flag_years = []
    
    return flag_years

# Indicative, Pettitt breakpoint check
def checkBreakPoint(its): 
    
    x = its.data.resample("D").sum().values
    x = x[~np.isnan(x)]
    x = x
    x = robjects.FloatVector(x)
    #print("made vector")
    
    #using the pettitt test
    #pet = trend.pettitt.test(x)
    pettitt = robjects.r['pettitt.test']
    #print("function available")
    pet = pettitt(x)
    #print("function done")
    y = pet.rx('p.value') #gives the p-value if p-value is below 0.05 (or 0.01) there might be a change point
    p = np.asarray(y)[0][0]
    if p < 0.01: #different
        return 1
    else:
        return 0
"""
++++++++++++++++++++++++++++++++++ Threshold Checks +++++++++++++++++++++++++++++++++++
"""
def worldRecordCheck(val): 
    """
    1h record1 with separate flags showing exceedance by <20%, >= 20%, 33%, or 50% - 
    world record = 394.5mm in 1 hour (http://www.nws.noaa.gov/oh/hdsc/record_precip/record_precip_world.html)
    
    *** updated to use 401.0 mm in 1 hour - compare e.g.
    http://www.nws.noaa.gov/oh/hdsc/record_precip/record_precip_world.html
    http://www.bom.gov.au/water/designRainfalls/rainfallEvents/worldRecRainfall.shtml
    https://wmo.asu.edu/content/world-meteorological-organization-global-weather-climate-extremes-archive
    """

    if val >= world_records['hourly'] * 1.5:
        return 4
    elif val >= world_records['hourly'] * 1.33:
        return 3
    elif val >= world_records['hourly'] * 1.2:
        return 2
    elif val >= world_records['hourly']:
        return 1
    else:
        return 0
    
def worldRecordCheckTs(its):
    
    wrcts = its.data.map(lambda x: worldRecordCheck(x))
    return list(wrcts)

"""
# Checks against ETCCDI indices +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
"""
# We are using [ETCCDI indicies](http://etccdi.pacificclimate.org/list_27_indices.shtml) to act as thresholds for expected hourly values.
# In particular, we are using index 17:
# Rx1day, Monthly maximum 1-day precipitation:
# Here I'm just going to start by using the maximum of the annual maximums,
# to give us the biggest possible daily value for each available square.
# First we must read in the indicies from the netCDF file:
# We then calculate the maximum rainfall value over the whole period for each gridsquare.    

# # ETCCDI utility function 1
# def prepETCCDIvariable(pathToNCfile, indexName, meanOrMax):
    
    # ds = xr.open_dataset(pathToNCfile)

    # # Calculate maximum rainfall value over whole period
    # if meanOrMax == 'max':
        # data = np.array(ds['Ann'].max(axis=0))
    # if meanOrMax == 'mean':
        # data = np.array(ds['Ann'].mean(axis=0))

    # # Make 2d arrays of x and y coordinates of regular grid
    # x = np.linspace(0, 357.5, 144)
    # y = np.linspace(-90, 90, 73)
    # xx, yy = np.meshgrid(x, y)

    # #Mask the array so that we can exclude NaN values
    # masked_array = ma.masked_invalid(data)

    # # Filter for valid points/values
    # x1 = xx[~masked_array.mask]
    # y1 = yy[~masked_array.mask]
    # tmp = masked_array[~masked_array.mask]

    # # Interpolate
    # # - if you have nans at the edges then you might need to set the 
    # #   fill_value unless you use method="nearest"
    # # - https://docs.scipy.org/doc/scipy-0.18.1/reference/generated/scipy.interpolate.griddata.html
    # interp_array = scipy.interpolate.griddata((x1, y1), tmp.ravel(), (xx,yy), method='nearest')

    # # convert back from a np array to an xarray DataArray for easy plotting
    # filledDA = xr.DataArray(interp_array, coords={'Latitude':y, 'Longitude':x}, dims=('Latitude', 'Longitude'), name=indexName)

    # return [data, filledDA]

# ETCCDI utility function 1 (revised)
def prepETCCDIvariable(input_path, index_name, aggregation, data_source):
    
    ds = xr.open_dataset(input_path)
    
    # Omit final year (2010) of HADEX2 - suspiciously large CDD for Malaysia
    if data_source == 'HADEX2':
        ds = ds.sel(time=slice(datetime.datetime(1951, 1, 1, 0), 
            datetime.datetime(2009, 12, 31, 23)))

    # Calculate maximum rainfall value over whole period
    vals = ds[index_name].values
    if index_name in ['CWD', 'CDD']:
        vals = ds[index_name].values.astype('timedelta64[s]')
        vals = vals.astype('float32') / (86400.0)
        vals[vals<0.0] = np.nan
    vals = ma.masked_invalid(vals)
    if aggregation == 'max':
        data = ma.max(vals, axis=0)
    if aggregation == 'mean':
        data = ma.mean(vals, axis=0)

    # Convert back from to a xarray DataArray for easy plotting
    # - masked array seems to be interpreted as np array (i.e. nans are present 
    # in the xarray DataArray
    data2 = xr.DataArray(data, coords={'Latitude': ds['lat'].values, 
        'Longitude': ds['lon'].values}, dims=('Latitude', 'Longitude'), 
        name=index_name)
    
    ds.close()

    return data2

def get_ETCCDI_value(index_name, lon, lat):
    
    lon = float(lon)
    lat = float(lat)
    
    # Check gauge longitude and convert to -180 - 180 range if necessary
    if lon > 180.0:
        lon = lon - 360.0
    
    # Array location indices for closest cell centre to gauge location
    location_indices = {'GHCNDEX': {}, 'HADEX2': {}}
    for data_source in location_indices.keys():
        location_indices[data_source]['lon'] = (np.argmin(
            np.abs(etccdi_data[data_source][index_name]['Longitude'].values - lon)))
        location_indices[data_source]['lat'] = (np.argmin(
            np.abs(etccdi_data[data_source][index_name]['Latitude'].values - lat)))
    
    # Maximum of ETCCDI index values from GHCNDEX and HADEX2 for cell
    etccdi_index_values = {}
    for data_source in location_indices.keys():
        yi = location_indices[data_source]['lat']
        xi = location_indices[data_source]['lon']
        etccdi_index_values[data_source] = etccdi_data[data_source][index_name].values[yi,xi]
    #max_index = max(list(etccdi_index_values.values()))
    etccdi_vals = np.asarray(list(etccdi_index_values.values()))
    if np.any(np.isfinite(etccdi_vals)):
        max_index = np.max(etccdi_vals[np.isfinite(etccdi_vals)])
    else:
        max_index = np.nan
    
    # For cases where no value for the cell, look in 3x3 window and take the maximum
    if np.isnan(max_index):
        etccdi_index_window = {}
        for data_source in location_indices.keys():
            yi = location_indices[data_source]['lat']
            xi = location_indices[data_source]['lon']
            window = etccdi_data[data_source][index_name].values[yi-1:yi+2,xi-1:xi+2]
            if np.any(np.isfinite(window)):
                etccdi_index_window[data_source] = np.max(window[np.isfinite(window)])
            else:
                etccdi_index_window[data_source] = np.nan
        
        window_vals = np.asarray(list(etccdi_index_window.values()))
        if np.any(np.isfinite(window_vals)):
            max_index_window = np.max(window_vals[np.isfinite(window_vals)])
        else:
            max_index_window = np.nan
        
    else:
        max_index_window = np.nan
    
    return max_index, max_index_window

# # ETCCDI utility function 2
# def convertCoords(x,y):
    # x = (int(x*10)/25)*2.5
    # y = (int(y*10)/25)*2.5

    # i = int((x + 0)/2.5)
    # j = int(35-(y/2.5))
    
    # return [i, j]

# ETCCDI utility function 3 - returns flag based on exceedence of parameters
# Replaces Rx1dayCheck, R99pTOTCheck, PRCPTOTCheck
def dayCheck(val, pMax, pMaxFilled): 
    
    if np.isnan(pMax):
        if val >= pMaxFilled*1.5:
            return 8
        elif val >= pMaxFilled*1.33:
            return 7
        elif val >= pMaxFilled*1.2:
            return 6
        elif val >= pMaxFilled:
            return 5
        else:
            return 0
    else:
        if val >= pMax*1.5:
            return 4
        elif val >= pMax*1.33:
            return 3
        elif val >= pMax*1.2:
            return 2
        elif val >= pMax:
            return 1
        else:
            return 0   

"""
++++++++ Rx1day: check hourly values against maximum 1-day precipitation   ++++++++
"""
# def getRx1day(x, y):
    # i, j = convertCoords(x, y)
    # EIndex = float(Rx1dayData[j][i])
    # EIndexFilled = float(Rx1dayFilled[j][i])
    # return [EIndex, EIndexFilled]
    
def Rx1dayCheckTs(its):
    
    #pMax, pMaxFilled = getRx1day(its.latitude, its.longitude)
    pMax, pMaxFilled = get_ETCCDI_value('Rx1day', its.longitude, its.latitude)
    df = its.data.to_frame("GSDR")
    
    ''' If you have a high density of daily gauges, you can calculate Rx1day stats from that and compare them to a daily total from the hourly gauges. The ETCCDI gauge density is not high enough to do this so we use it as a threshold check for hourly values
    
    df["roll"] = np.around(df.GSDR.rolling(window=24, center=False, min_periods=24).sum())
    df["r1dcts"] = df.roll.map(lambda x: dayCheck(x, pMax, pMaxFilled))
    '''
    if np.isfinite(pMax) or np.isfinite(pMaxFilled):
        df["r1dcts"] = df.GSDR.map(lambda x: dayCheck(x, pMax, pMaxFilled))
    else:
        df["r1dcts"] = np.nan
    
    return list(df.r1dcts)

"""
++++++++ Other precipitation index checks ++++++++
"""
# Indicative check against R99pTOT: R99pTOT. Annual total PRCP when RR > 99p: 
# def getR99pTOT(x, y): 
    # i, j = convertCoords(x, y)
    # EIndex = float(R99pTOTData[j][i])
    # EIndexFilled = float(R99pTOTFilled[j][i])
    # return [EIndex, EIndexFilled]

def R99pTOTCheckAnnual(its): 
    
    #pMax, pMaxFilled = getR99pTOT(its.latitude, its.longitude) #pMax, pMaxFilled = 100, 100#
    pMax, pMaxFilled = get_ETCCDI_value('R99p', its.longitude, its.latitude)
    
    if np.isfinite(pMax) or np.isfinite(pMaxFilled):
    
        dailyTs = its.data.resample("D").sum() #this changes depending on which version of pandas youre using. o.14 requires how agument, later requires .sum
        
        perc99 = dailyTs.groupby(pd.Grouper(freq='A')).quantile(.99)
        py = list(perc99.index.year)
        pv = list(perc99)
        pDict = {}
        for p in range(len(py)):
            pDict[py[p]] = pv[p]
        #print(pDict)
        dailyDf = dailyTs.to_frame("daily")
        dailyDf["year"] = dailyDf.index.year
        dailyDf["p99"] = dailyDf.apply(lambda row: pDict[row.year], axis=1)
        dailyDf["filtered"] = dailyDf.daily.where(dailyDf.daily >= dailyDf.p99)
        perc99Tot = dailyDf.groupby(pd.Grouper(freq='A')).sum()
        tots = list(perc99Tot.filtered)
        checks = [dayCheck(t, pMax, pMaxFilled) for t in tots]
    
    else:
        checks = [np.nan]
    
    return checks
 
# Indicative check against annual total: PRCPTOT. Annual total precipitation in wet days:
# def getPRCPTOT(x, y): 
    # i, j = convertCoords(x, y)
    # EIndex = float(PRCPTOTData[j][i])
    # EIndexFilled = float(PRCPTOTFilled[j][i])
    # return [EIndex, EIndexFilled] 
        
def PRCPTOTCheckAnnual(its): 
    
    #pMax, pMaxFilled = getPRCPTOT(its.latitude, its.longitude)
    pMax, pMaxFilled = get_ETCCDI_value('PRCPTOT', its.longitude, its.latitude)
    
    if np.isfinite(pMax) or np.isfinite(pMaxFilled):
        annTots = its.data.groupby(pd.Grouper(freq='A')).sum()
        tots = list(annTots)
        checks = [dayCheck(t, pMax, pMaxFilled) for t in tots]
    else:
        checks = [np.nan]
    
    return checks

"""
++++++++ Long wet/dry spell checks ++++++++
"""
# ETCCDI provide an index for maximum length of wet spell.
# We can use this to see if there are a suspicious number of consecutive wet hours recorded.
# Consecutive Wet Days: Maximum length of wet spell, maximum number of consecutive days with RR = 1mm: 
# Let RRij be the daily precipitation amount on day i in period j.
# Count the largest number of consecutive days where: RRij = 1mm

# Helper function, flags data based on various thresholds
def SpellCheck(val, longestWetPeriod, longestWetPeriodFilled): 
    
    if np.isnan(longestWetPeriod): 
        if val >= longestWetPeriodFilled*24*1.5:
            return 8
        elif val >= longestWetPeriodFilled*24*1.33:
            return 7
        elif val >= longestWetPeriodFilled*24*1.2:
            return 6
        elif val >= longestWetPeriodFilled*24:
            return 5
        else:
            return 0
    else:
        if val >= longestWetPeriod*24*1.5:
            return 4
        elif val >= longestWetPeriod*24*1.33:
            return 3
        elif val >= longestWetPeriod*24*1.2:
            return 2
        elif val >= longestWetPeriod*24:
            return 1
        else:
            return 0 
        
# def getCWD(x, y): 
    # i, j = convertCoords(x, y)
    # EIndex = float(CWDData[j][i])
    # EIndexFilled = float(CWDFilled[j][i])
    # return [EIndex, EIndexFilled]

# Calculate length of consecutive wet days and their location in the rainfall series
def getWetPeriods(vals):
    daily = vals.groupby(lambda x: x.floor('1D')).aggregate(lambda x: np.sum(x))
    
    startDayIndexList = []
    startIndexList = []
    durationList = []
    
    wetFlag = 0
    dayTicker = 0
    
    for i in range(len(daily)):
        v = daily.iloc[i]
        
        if v >= 1.0:
            if wetFlag == 0:
                startDayIndexList.append(daily.index[i])
            dayTicker += 1
            wetFlag = 1
        else:
            if wetFlag == 1:
                durationList.append(dayTicker)
            dayTicker = 0
            wetFlag = 0     
        
        if i == len(daily)-1:
            if wetFlag == 1:
                durationList.append(dayTicker)
                
    
    # Convert date list to index list
    for i in range(len(startDayIndexList)):
        if ((i==0) & (startDayIndexList[i] < vals.index[i])):
            startIndexList.append(0)
        else:
            startIndexList.append(vals.index.get_loc(startDayIndexList[i]))
    
    # Convert day length to hourly length:
    durationList = list(np.dot(24,durationList))
    
    return[startIndexList, durationList] 

def CWDCheck(its): 
    
    vals = its.data
    #longestWetPeriod, longestWetPeriodFilled = getCWD(its.latitude, its.longitude)
    longestWetPeriod, longestWetPeriodFilled = get_ETCCDI_value('CWD', its.longitude, its.latitude)
    startIndexList, durationList = getWetPeriods(vals)
    flagsList = [0 for i in range(len(vals))]
    
    if np.isfinite(longestWetPeriod) or np.isfinite(longestWetPeriodFilled):
    
        for wetPeriod in range(len(startIndexList)):
            flag = SpellCheck(durationList[wetPeriod], longestWetPeriod, longestWetPeriodFilled)
            
            for j in range(startIndexList[wetPeriod], min(startIndexList[wetPeriod]+durationList[wetPeriod],(len(flagsList)-1)), 1):
                flagsList[j] = flag
    
    else:
        flagsList = [np.nan for i in range(len(vals))]
    
    return flagsList 


# ### Long dry spells
 
# ETCCDI provide an index for maximum length of dry spell.
# We can use this to see if there are a suspicious number of consecutive dry hours recorded.
# Consecutive Dry Days: Maximum length of dry spell, maximum number of consecutive days with RR < 1mm: 
# Let RRij be the daily precipitation amount on day i in period j.
# Count the largest number of consecutive days where: RRij < 1mm

# def getCDD(x, y): 
    # i, j = convertCoords(x, y)
    # EIndex = float(CDDData[j][i])
    # EIndexFilled = float(CDDFilled[j][i])
    # return [EIndex, EIndexFilled]

def getDryPeriods(vals): 
    
    startIndexList = []
    durationList = []
    
    dryFlag = 0
    hoursTicker = 0

    for i in range(len(vals)):
        v = vals[i]

        
        if v == 0:
            if dryFlag == 0:
                startIndexList.append(i)
            
            hoursTicker +=1
            dryFlag = 1
            if i == len(vals)-1:
                durationList.append(hoursTicker)
            
        else:
            if dryFlag == 1:
                durationList.append(hoursTicker)
                
            hoursTicker = 0
            dryFlag = 0
            
        if i == len(vals):
            if dryFlag == 1:
                durationList.append(hoursTicker)
                
    return [startIndexList, durationList]   

def CDDCheck(its): 
  
    vals = list(its.data)
    #longestDryPeriod, longestDryPeriodFilled = getCDD(its.latitude, its.longitude)
    longestDryPeriod, longestDryPeriodFilled = get_ETCCDI_value('CDD', its.longitude, its.latitude)

    startIndexList, durationList = getDryPeriods(vals)
    flagsList = [0 for i in range(len(vals))]
    
    if np.isfinite(longestDryPeriod) or np.isfinite(longestDryPeriodFilled):
    
        for dryPeriod in range(len(startIndexList)):
            flag = SpellCheck(durationList[dryPeriod], longestDryPeriod, longestDryPeriodFilled)
            
            for j in range(startIndexList[dryPeriod], startIndexList[dryPeriod]+durationList[dryPeriod], 1):
                flagsList[j] = flag
    else:
        flagsList = [np.nan for i in range(len(vals))]
    
    return flagsList


"""
++++++++++++++++++++++++++++++++++ Non-Threshold Checks +++++++++++++++++++++++++++++++++++
""" 

# def getSDII(x, y): 
    # i, j = convertCoords(x, y)
    # EIndex = float(SDIIData[j][i])
    # EIndexFilled = float(SDIIFilled[j][i])
    # return [EIndex, EIndexFilled]

def get_SDII(its):
    # *** CHECK HOURLY WORLD RECORD PRECIPITATION ***
    # ?? insert a check for whether gauge SDII exceeds minimum tip / resolution/precision ??
    
    # Remove any hours exceeding world record in the gauge record
    df1 = its.data.copy().to_frame()
    df1.columns = ['val']
    df1['val'] = np.where(df1['val'] > world_records['hourly'], np.nan, df1['val'])
    
    # Aggregate gauge to daily and remove any days exceeding world record
    # - remove first and last days assuming might be incomplete
    #df1 = df1[df1.notnull()]
    #df2 = df1.groupby(pd.Grouper(freq='D')).sum()
    df2 = df1.resample("D", label='left', closed='right').apply(lambda x: x.values.sum())
    df2 = df2.loc[(df2.index > df2.index.min()) & (df2.index < df2.index.max())]
    df2['val'] = np.where(df2['val'] > world_records['daily'], np.nan, df2['val'])
    
    # Calculate SDII from gauge
    prcp_sum = df2.loc[df2['val'] >= 1.0, 'val'].sum()
    wetday_count = df2.loc[df2['val'] >= 1.0, 'val'].count()
    sdii_gauge = prcp_sum / float(wetday_count)
    
    # Retrieve SDII from gridded ETCCDI datasets
    sdii_cell, sdii_filled = get_ETCCDI_value('SDII', its.longitude, its.latitude)
    if np.isfinite(sdii_cell):
        sdii_gridded = sdii_cell
    else:
        if np.isfinite(sdii_filled):
            sdii_gridded = sdii_filled
        else:
            sdii_gridded = np.nan
    
    return [sdii_gridded, sdii_gauge]

"""
++++++++ Daily accumulation checks ++++++++
"""
def dailyAccumsDayCheck(dayList, meanWetDayVal, meanWetDayValFilled): 
    """
    Suspect daily accumulations flagged where a recorded rainfall amount at these times is preceded by 23 hours with no rain.  
    A threshold of 2x the mean wet day amount for the corresponding month is applied to increase the chance of identifying 
    accumulated values at the expense of genuine, moderate events.
    """

    # if dayList[23] > 0:
        # dryHours = 0
        # for i in range(23):
            # if dayList[i] <=0:
                # dryHours +=1
        # if dryHours == 23:
            # if np.isnan(meanWetDayVal): 
                # if dayList[23] > meanWetDayValFilled*2:
                    # return 2
                # else:
                    # return 0
            # else:
                # if dayList[23] > meanWetDayVal*2:
                    # return 1
                # else:
                    # return 0
        # else:
            # return 0
    # else:
        # return 0
    
    # ---
    # Alternative implementation - same logic as above
    
    if dayList[23] > 0:
        dryHours = 0
        for i in range(23):
            if dayList[i] <=0:
                dryHours +=1
        if dryHours == 23:
            if np.isnan(meanWetDayVal): 
                #if dayList[23] > meanWetDayValFilled*2:
                if dayList[23] > meanWetDayValFilled:
                    #return 2
                    flag = 2
                else:
                    #return 0
                    flag = 0
            else:
                #if dayList[23] > meanWetDayVal*2:
                if dayList[23] > meanWetDayVal:
                    #return 1
                    flag = 1
                else:
                    #return 0
                    flag = 0
        else:
            #return 0
            flag = 0
    else:
        #return 0
        flag = 0
    
    return flag
    
def dailyAccumsCheck(its): 
    
    vals = list(its.data)
    
    #meanWetDayVal, meanWetDayValFilled = getSDII(its.latitude, its.longitude)
    meanWetDayVal, meanWetDayValFilled = get_SDII(its)
    
    flags = [0 for i in range(len(vals))]
    
    for i in range(len(vals)-24):
        dayValList = vals[i:i+24]
        flag = dailyAccumsDayCheck(dayValList, meanWetDayVal, meanWetDayValFilled)
        if flag > max(flags[i:i+24]):
            flags[i:i+24] = [flag for j in range(24)]
    
    return flags

"""
++++++++ Monthly accumulation checks ++++++++
"""       
def monthlyAccumsDayCheck(monthList, meanWetDayVal, meanWetDayValFilled): 
    """Suspect monthly accumulations.  
    Identified where only one hourly value is reported over a period of a month
    and that value exceeds the mean wet hour amount for the corresponding month."""

    if monthList[719] > 0:
        dryHours = 0
        for i in range(719):
            if monthList[i] <=0:
                dryHours +=1
        if dryHours == 719:
            if np.isnan(meanWetDayVal): 
                if monthList[719] > meanWetDayValFilled*2:
                    return 2
                else:
                    return 0
            else:
                if monthList[719] > meanWetDayVal*2:
                    return 1
                else:
                    return 0
        else:
            return 0
    else:
        return 0

# Flags month prior to high value
# def monthlyAccumsCheck(its): 
    
    # vals = list(its.data)
    # #meanWetDayVal, meanWetDayValFilled = getSDII(its.latitude, its.longitude)
    # meanWetDayVal, meanWetDayValFilled = get_SDII(its)
    
    # flags = [0 for i in range(len(vals))]
    
    # for i in range(len(vals)-720):
        # monthValList = vals[i:i+720]
        # flag = monthlyAccumsDayCheck(monthValList, meanWetDayVal, meanWetDayValFilled)
        
        # # Modify flag if the 24 hours following the wet hour are dry
        # if flag > 0:
            # if sum(vals[i+720:i+720+24]) == 0:
                # if np.isnan(meanWetDayVal):
                    # flag = 4
                # else:
                    # flag = 3
        
        # if flag > max(flags[i:i+720]):
            # flags[i:i+720] = [flag for j in range(720)]
        
        # # Alternative implementation where the 24 hours following the wet hour are flagged
        # # if flag in [1,2]:
            # # if flag > max(flags[i:i+720]):
                # # flags[i:i+720] = [flag for j in range(720)]
        # # elif flag in [3,4]:
            # # if flag > max(flags[i:i+720+24]):
                # # flags[i:i+720+24] = [flag for j in range(720+24)]
        
    # return flags

# Attempy to speed up monthly accumulations check with an alternative implementation
def monthlyAccumsCheck(its):
    
    # Find threshold for wet hour following dry month (2 * mean wet day rainfall)
    mean_wetday_val, mean_wetday_val_filled = get_SDII(its)
    if np.isnan(mean_wetday_val):
        threshold = mean_wetday_val_filled * 2.0
    else:
        threshold = mean_wetday_val * 2.0
    
    # Lag values forwards and backwards to help identify consecutive value streaks
    df = its.data.copy().to_frame()
    df.columns = ['val']
    df['prev'] = df.shift(1)['val']
    df['next'] = df.shift(-1)['val']
    #df['prev'] = np.where(df['prev'].isnull(), 0, df['prev'])
    #df['next'] = np.where(df['next'].isnull(), 0, df['next'])
    
    #print(df.loc[np.isnan(df['val'])])
    #print(df.loc[(df.index >= datetime.datetime(2007, 1, 13, 0)) & (df.index <= datetime.datetime(2007, 1, 13, 23))])
    #print(df.loc[(df.index >= datetime.datetime(2007, 5, 25, 0)) & (df.index <= datetime.datetime(2007, 5, 25, 23))])
    #print(df.loc[(df.index >= datetime.datetime(2007, 5, 26, 0)) & (df.index <= datetime.datetime(2007, 5, 26, 23))])
    
    # Look for streaks of consecutive zeros followed by a wet hour
    start_inds = np.flatnonzero(((df.prev > 0.0) | (np.isnan(df.prev))) & (df.val == 0.0) & 
        (df.next == 0.0))
    end_inds = np.flatnonzero((df.prev == 0.0) & (df.val == 0.0) & 
        ((df.next > 0.0) | (np.isnan(df.next)))) + 1
    
    # Check whether any periods identified (if not then may be a very high 
    # proportion of missing data
    if start_inds.shape[0] > 0:
        
        # Check whether final start index has a corresponding end index
        if end_inds.shape[0] == (start_inds.shape[0] - 1):
            end_inds = end_inds.tolist()
            end_inds.append(start_inds[-1])
            end_inds = np.asarray(end_inds)
        
        # Check whether final end index is out of array bounds (by 1)
        # - this occurs if final period stretches to the end of the dataframe,
        # where 'next' will be a nan
        if int(end_inds[-1]) == len(df['val']):
            end_inds[-1] -= 1
        
        # Summary dataframe of periods
        df1 = pd.DataFrame(
            dict(start = start_inds, end = end_inds))
        df1['diff'] = df1['end'] - df1['start'] + 1
        
        # Subset on periods with length of >= 720 days
        df1 = df1.loc[df1['diff'] >= 720]
        
        # Code below would adjust any periods >720 days to be =720 days (i.e. 
        # alter period start date) - not currently used
        #df1['start2'] = np.where(df1['diff'] > 720, 
        #    df1['start'] + (df1['diff'] - 720), df1['start'])
        
        # Filter on periods where end wet hour exceeds threshold (greater than 
        # 2 * mean wet day rainfall)
        df1['end_rainfall'] = np.nan
        i = 0
        for row in df1.iterrows():
            idx = int(row[1].end)
            df1.iloc[i, df1.columns.get_loc('end_rainfall')] = (
                df.iloc[idx, df.columns.get_loc('val')])
            i += 1
        df2 = df1.loc[df1['end_rainfall'] > threshold].copy()
        
        # Find out if the 23 hours following the wet hour are wet or dry 
        # (0=dry, 1=wet (any rain))
        df2['end_nextday_wet'] = 0
        i = 0
        for row in df2.iterrows():
            idx = int(row[1].end)
            rainfall_sum = df.iloc[idx+1:idx+1+23, df.columns.get_loc('val')].sum()
            if rainfall_sum > 0.0:
                df2.iloc[i, df2.columns.get_loc('end_nextday_wet')] = 1
            #else:
            #    df2.iloc[i, df2.columns.get_loc('end_nextday_wet')] = 0
            i += 1
        #df2 = df2.loc[df2['end_nextday_wet'] == 0]
        
        # Define flags
        flag = 1
        if np.isnan(mean_wetday_val):
            flag = 2
        df2['flag'] = flag
        df2['flag'] = np.where(df2['end_nextday_wet'] == 1, df2['flag'] + 2, 
            df2['flag'])
        
        # Make list of flags
        flags = [0 for i in range(len(df['val']))]
        for row in df2.iterrows():
            for i in range(int(row[1].start), int(row[1].end+1)):
                flags[i] = int(row[1].flag)
    
    # If no periods identified (e.g. lots of missing data) return nans
    else:
        flags = [np.nan for i in range(len(df['val']))]
    
    return flags

# """
# ++++++++ Streak checks ++++++++
# """
# def streaksCheck(its): 
    # '''
    # Streaks: This is where you see the same value repeated in a run. 
    # Currently this records streaks of 2hrs in a row or more over 2 x Monthly mean rainfall. 
    # It is considered to be unlikely that you would see even 2 consecutive large rainfall amounts. 
    # For this code I have substituted the monthly mean rainfall for SDII as I want the thresholds 
    # to be independent of the rainfall time series as the global dataset is of highly variable quality.
    # '''
    
    # vals=list(its.data)
    # meanWetDayVal, meanWetDayValFilled = getSDII(its.latitude, its.longitude)
    
    # streaksFlags = [0 for i in range(len(vals))]
    
    # for i in range(1, len(vals), 1):
        # if np.isnan(meanWetDayVal):       
            # if vals[i] > meanWetDayValFilled*2:
                # if vals[i] == vals[i-1]:
                    # streaksFlags[i] = 2
                    # streaksFlags[i-1] = 2
        # else:
            # if vals[i] > meanWetDayVal*2:
                # if vals[i] == vals[i-1]:
                    # streaksFlags[i] = 1
                    # streaksFlags[i-1] = 1
                
    # return streaksFlags

def streaksCheck(its):
    
    # Find wet day rainfall threshold (for streaks of any length)
    #mean_wetday_val, mean_wetday_val_filled = getSDII(its.latitude, its.longitude)
    mean_wetday_val, mean_wetday_val_filled = get_SDII(its)
    threshold = mean_wetday_val * 2.0
    if np.isnan(mean_wetday_val):
        threshold = mean_wetday_val_filled * 2.0
    
    # Lag values forwards and backwards to help identify consecutive value streaks
    df = its.data.copy().to_frame()
    df.columns = ['val']
    df['prev'] = df.shift(1)['val']
    df['next'] = df.shift(-1)['val']
    df['prev'] = np.where(df['prev'].isnull(), 0, df['prev'])
    df['next'] = np.where(df['next'].isnull(), 0, df['next'])
    
    # Look for streaks of values exceeding 2 * mean wet day rainfall
    df1 = pd.DataFrame(
        dict(start = np.flatnonzero((df.val != df.prev) & (df.val == df.next) & 
                (df.val >= threshold)),
            end = np.flatnonzero((df.val == df.prev) & (df.val != df.next) & 
                (df.val >= threshold))))
    df1['diff'] = df1['end'] - df1['start'] + 1
    
    # Calculate annual minimum data value >0 in each year (added FL09)
    df99 = df[df['val'] > 0.0].groupby(df[df['val'] > 0.0].index.year)['val'].agg('min')
    df99 = df99.to_frame()
    df99.rename({'val': 'year_min'}, axis=1, inplace=True)
    df99['year'] = df99.index
    
    # Ensure that year_min is not too small (FL10) - set minimum as 0.1
    # - issues with small numbers, especially where minimum changes between years
    # - also ensure not too large (<= 2.6, i.e. US tip resolution)
    df99['year_min'] = np.where(df99['year_min'] < 0.1, 0.1, df99['year_min'])
    df99['year_min'] = np.where(df99['year_min'] > 2.6, 2.6, df99['year_min'])
    
    # Add annual minimum data value >0 as column (added FL09)
    df['year'] = df.index.year
    df['datetime'] = df.index
    df = df.merge(df99, how='left', left_on='year', right_on='year')
    df.drop('year', axis=1, inplace=True)
    df.set_index('datetime', inplace=True)
    
    # Look for streaks of consecutive values (changed from >0 to any 
    # magnitude > year minimum data value above 0 in FL09)
    try:
        df2 = pd.DataFrame(
            dict(start = np.flatnonzero((df.val != df.prev) & (df.val == df.next) & 
                    (df.val > df.year_min)),
                end = np.flatnonzero((df.val == df.prev) & (df.val != df.next) & 
                    (df.val > df.year_min))))
    
    # If above fails then use one value for all years as threshold, based on 
    # maximum of annual minima, ensuring >= 0.1 and <= 2.6 (done above) (FL10)
    except:
        min_threshold = np.max(df99['year_min'])
        df2 = pd.DataFrame(
            dict(start = np.flatnonzero((df.val != df.prev) & (df.val == df.next) & 
                    (df.val > min_threshold)),
                end = np.flatnonzero((df.val == df.prev) & (df.val != df.next) & 
                    (df.val > min_threshold))))
    
    # Subset on periods of >= 12 consecutive values
    df2['diff'] = df2['end'] - df2['start'] + 1
    df2 = df2.loc[df2['diff'] >= 12]
    
    # Commented out below in FL10 
    # # Look for streaks of >=24 consecutive values equal to minimum value
    # # > 0 (added FL09)
    # df2a = pd.DataFrame(
        # dict(start = np.flatnonzero((df.val != df.prev) & (df.val == df.next) & 
                # (df.val <= min_threshold)),
            # end = np.flatnonzero((df.val == df.prev) & (df.val != df.next) & 
                # (df.val <= min_threshold))))
    # df2a['diff'] = df2a['end'] - df2a['start'] + 1
    # df2a = df2a.loc[df2a['diff'] >= 24]
    
    # Combine dataframes listing periods to be flagged
    # - in FL09 removed 'if np.isnan(mean_wetday_val)' clause for df2 flag (i.e.
    # > minimum tip streaks for >= 12 hours) as mean_wetday_val is not relevant
    # --> so now flag 4 is reserved for == minimum tip streaks (>= 24 hours)
    # ----> flag 4 not used in FL10
    flag = 1
    if np.isnan(mean_wetday_val):
        flag = 2
    df1['flag'] = flag
    # flag = 3
    # if np.isnan(mean_wetday_val):
        # flag = 4
    # df2['flag'] = flag
    df2['flag'] = 3
    #df2a['flag'] = 4 # FL10
    df3 = df1.append(df2)
    #df3 = df3.append(df2a) # FL10
    
    # Make list of flags
    flags = [0 for i in range(len(df['val']))]
    for row in df3.iterrows():
        for i in range(row[1].start, row[1].end+1):
            flags[i] = row[1].flag
    
    return flags

"""
++++++++ Change in minimum value check ++++++++
"""
# def changeInMinValCheck(its): 
    # '''
    # Change in minimum value: This is an homogeneity check to see if the resolution of the data has changed. 
    # Currently, I am including a flag if there is a year of no data as that seems pretty bad to me.
    # '''

    # dates = list(its.data.index)
    # vals = list(its.data.values)
    
    # changeFlag = 0
    # changeYears = []
    
    # minYear = dates[0].year
    # maxYear = dates[-1].year
    
    # numYears = (maxYear-minYear) + 1
    
    # valsYears = [[] for i in range(numYears)]
    
    # for i in range(len(dates)):
        # if vals[i]>0:
            # valsYears[dates[i].year - minYear].append(vals[i])
            
    # #valsYears = []
    # for miniList in valsYears:
        # try:
            # valsYears.append(min(miniList))
        # except:
            # valsYears.append(np.nan)
    
    # if len(valsYears)>1:
        # for i in range(1, numYears+1 ,1):
            # if valsYears[i] == valsYears[i-1]:
                # pass
            # else:
                # changeFlag = 1
                # changeYears.append(minYear+i)
    # else:
        # changeFlag=0
        # changeYears = []
            
    # return [changeFlag, changeYears]
    
# Alternative implementation to return list of years where the minimum value >0
# differs from the data precision/resolution identified in the raw (pre-QC) files
def changeInMinValCheck(its):
    
    # Filter on values >0
    df = its.data[its.data>0.0].to_frame()
    df.columns = ['val']
    
    # Find minimum by year
    df = df.groupby(df.index.year).min()
    
    # List of years differing from inferred precision in raw (pre-QC) data files
    df = df.loc[df['val'] != its.resolution]
    flag_years = df.index.tolist()
    if len(flag_years) > 0:
        change_flag = 1
    else:
        change_flag = 0
    
    return [change_flag, flag_years]

"""
++++++++++++++++++++++++++ Neighbour Checks - Basic functions +++++++++++++++++++++++++++
"""
# Coordinate system conversion
def geodetic_to_ecef(lat, lon, h):
  
    a = 6378137
    b = 6356752.3142
    f = (a - b) / a
    e_sq = f * (2-f)
    # (lat, lon) in WSG-84 degrees
    # h in meters
    lamb = math.radians(lat)
    phi = math.radians(lon)
    s = math.sin(lamb)
    N = a / math.sqrt(1 - e_sq * s * s)

    sin_lambda = math.sin(lamb)
    cos_lambda = math.cos(lamb)
    sin_phi = math.sin(phi)
    cos_phi = math.cos(phi)

    x = (h + N) * cos_lambda * cos_phi
    y = (h + N) * cos_lambda * sin_phi
    z = (h + (1 - e_sq) * N) * sin_lambda

    return x, y, z

# Helper function, finds hourly neighbour stations ++++++++++++++++++++++++++++
def findHourlyNeighbours(target):  
  
  # float("nan") returns np.nan so needs to be handled separately (occurs in some Italy (Sicily) files)
  # whereas float("NA") returns value error (i.e. convention in most raw/formatted files)
  try:
    if elv != "nan":
      elv = float(target.elevation)
    else:
      elv = 100
  except:
    elv = 100
  
  converted_hourlyCoords = geodetic_to_ecef(target.latitude, target.longitude, elv)
  dist, index = hourlynTree.query(converted_hourlyCoords, k=30) #K needs to be equal or less than the number of stations available in the database
  overlap = []
  pairedStations = []
  distance = []
  paths = []

  hourlyDates = (target.start_datetime, target.end_datetime)

  counter = 0
  for i in range(len(dist)):
    dci = index[i]
    pol, ol = calculate_overlap(hourlyDates, hourlynDates[dci])
    ps = hourlynNames[dci]
    di = dist[i]
    pa = hourlynPaths[dci]
      
    if di < 50000: #must be within 50km
      if ol > 365*3: #must have at least 3 years overlap
        #if counter < 10: #want to select the closest 10
        if counter < 11: #want to select the closest 10, but the first one is always the target itself
          overlap.append(ol)
          pairedStations.append(ps)
          distance.append(di)
          paths.append(pa)
          counter += 1
          #print("found one")

  if len(pairedStations)>=3:
    return [pairedStations, paths] 
  else:
    #return None
    return [[], []]

# Helper function, finds daily neighbour stations +++++++++++++++++++++++++++++
def findDailyNeighbours(target):
  
  try:
    elv = float(target.elevation)
  except:
    elv = 100
    
  converted_hourlyCoords = geodetic_to_ecef(target.latitude, target.longitude, elv)
  
  dist, index = tree.query(converted_hourlyCoords, k=30)
  
  overlap = []
  pairedStations = []
  distance = []

  hourlyDates = (target.start_datetime, target.end_datetime)
    
  counter = 0
  for i in range(len(dist)):
    dci = index[i]
    pol, ol = calculate_overlap(hourlyDates, dailyDates[dci])
    ps = dailyNames[dci]
    di = dist[i]
    #print(dci, ps, di, ol)
      
    if di < 50000: #must be within 50km
      if ol > 365*3: #must have at least 3 years overlap
        if counter < 10: #want to select the closest 10
          overlap.append(ol)
          pairedStations.append(ps)
          distance.append(di)
          counter += 1
      
  if len(pairedStations)>=3:
    return pairedStations 
  else:
    #return None
    return []

# Helper function, finds daily neighbour stations +++++++++++++++++++++++++++++
def findMonthlyNeighbours(target):
  
  try:
    elv = float(target.elevation)
  except:
    elv = 100
    
  converted_hourlyCoords = geodetic_to_ecef(target.latitude, target.longitude, elv)
  
  dist, index = monthlyTree.query(converted_hourlyCoords, k=30)
  
  overlap = []
  pairedStations = []
  distance = []

  hourlyDates = (target.start_datetime, target.end_datetime)
    
  counter = 0
  for i in range(len(dist)):
    mci = index[i]
    pol, ol = calculate_overlap(hourlyDates, monthlyDates[mci])
    ps = monthlyNames[mci]
    di = dist[i]
      
    if di < 50000: #must be within 50km
      if ol > 365*3: #must have at least 3 years overlap
        if counter < 10: #want to select the closest 10
          overlap.append(ol)
          pairedStations.append(ps)
          distance.append(di)
          counter += 1

  if len(pairedStations)>=3:
    return pairedStations 
  else:
    return None

#++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
def calculate_overlap(period1, period2):
    #taking period2 as the reference (daily) period
    r1, r2 = period2
    t1, t2 = period1
    
    if t1>=r1 and t2<=r2:
      p = 100
      rd = (t2-t1).days
      
    elif t1<r1 and t2<r1:
      p = 0
      rd = 0
      
    elif t1>r2 and t2>r2:
      p = 0
      rd = 0
      
    elif t1<=r1 and t2>=r2:
      rd = (r2-r1).days
      td = (t2-t1).days
      
      #p = rd*100/td
      try:
        p = rd*100/td
      except:
        p = -999
      
    elif t1<r1 and t2>=r1 and t2<r2:
      rd = (t2-r1).days
      td = (t2-t1).days
      
      #p = rd*100/td
      try:
        p = rd*100/td
      except:
        p = -999
      
    elif t1>r1 and t1<=r2 and t2>r2:
      rd = (r2-t1).days
      td = (t2-t1).days
      
      #p = rd*100/td
      try:
        p = rd*100/td
      except:
        p = -999
      
    else:
      p = -999
      
    return p, rd

"""
++++++++++++++++++++++++++++++++++ GPCC functions +++++++++++++++++++++++++++++++++++ LIZ WILL CHANGE THIS
"""
def getGPCC(startYear, endYear, gpccId):

  #p = subprocess.Popen(["get_zeitreihe_tw_by_id.sh", str(startYear), str(endYear), gpccId], cwd="/media/nas/x21971/GPCC_daily")
  #p.wait()
  gpcc_filename = "tw_" + gpccId + ".dat"
  dat_path = "/media/nas/x21971/GPCC_daily2/tw_" + gpccId + ".dat"
  zip_path = "/media/nas/x21971/GPCC_daily2/tw_" + gpccId + ".zip"
  if not os.path.exists(zip_path):
    if not os.path.exists(dat_path):
      p = subprocess.Popen(["get_zeitreihe_tw_by_id.sh", str(startYear), str(endYear), gpccId], 
        cwd="/media/nas/x21971/GPCC_daily2")
      p.wait()
      time.sleep(0.1)
      
      # Move retrieved .dat file to its own .zip folder
      with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.write(dat_path, arcname=gpcc_filename, compress_type=zipfile.ZIP_DEFLATED)
      time.sleep(0.1)
      os.remove(dat_path)
  
  #f = open("/media/nas/x21971/GPCC_daily/tw_" + gpccId + ".dat", "r")
  zf = zipfile.ZipFile(zip_path, "r")
  f = zf.open(gpcc_filename, "r")
  f.readline()
  
  dates = []
  vals = []
  
  file_format_error = 0
  try_again = 1
  for line in f:
    lineList=line.rstrip().split()
    # inserted try as at least one file has a dubious second header line
    try:
      dates.append(datetime.date(int(lineList[2]), int(lineList[1]), int(lineList[0])))
      read_values = 1
    except:
      if try_again == 1:
        try_again = 0
        read_values = 0
      elif try_again == 0:
        file_format_error = 1
        read_values = 0
        break
    if read_values == 1:
      gotOne = 0
      for v in lineList[3:]:
        if float(v) >= 0:
          vals.append(float(v))
          gotOne = 1
          break
      if gotOne==0:
        vals.append(np.nan)
  
  f.close()
  zf.close()
  
  if file_format_error == 0:
    ddf = pd.DataFrame(vals, index=dates, columns=["GPCC"])
    
    # Remove duplicate rows if present and check against unique index values
    ddf['date_tmp'] = ddf.index.copy()
    idx = ddf.index.drop_duplicates().copy()
    ddf.drop_duplicates(inplace=True)
    ddf.sort_index(inplace=True)
    ddf.drop(['date_tmp'], axis=1, inplace=True)
    #print(file_format_error, ddf.shape[0], idx.shape[0])
    if ddf.shape[0] != idx.shape[0]:
      vals = []
      dates = []
      ddf = pd.DataFrame(vals, index=dates, columns=["GPCC"])
      #print(file_format_error, ddf.shape[0])
    
  elif file_format_error == 1:
    vals = []
    dates = []
    ddf = pd.DataFrame(vals, index=dates, columns=["GPCC"])
    #print(file_format_error, ddf.shape[0])
  
  #ddf['GPCC'] = np.around(ddf['GPCC'], 1)
  
  return ddf

# Helper function to access Global Sub Daily Rainfall database (a.k.a. Intense Database)
def getGSDR(gsdrId, path):
  zfh = zipfile.ZipFile(path, "r")

  d = zfh.open(gsdrId + ".txt", mode="r")
  df = ex.readIntense(d, only_metadata=False, opened=True).data.to_frame("GSDR")
  d.close()
  zfh.close()
  
  # convert hourly to daily 7am-7am
  #df["roll"] = np.around(df.GSDR.rolling(window=24, center=False, min_periods=24).sum())
  df["roll"] = np.around(df.GSDR.rolling(window=24, center=False, min_periods=24).sum(), 1)

  dfd = df[df.index.hour==7]
  dts = list(dfd.index)
  dailyVals = list(dfd.roll)
  dday = []
  for hday in dts:
    s0 = hday-datetime.timedelta(days=1)
    dday.append(datetime.date(s0.year, s0.month, s0.day))
    
  gsdr = pd.Series(dailyVals, index=dday).to_frame("ts2")

  return gsdr

# Helper function to access Global Precipitation Climatology Centre monthly data... Ask Liz what is needed to implement this!      
def getMonthlyGPCC(startYear, endYear, gpccId): #Hey liz! Check this once you have access to monthly!

  #p = subprocess.Popen(["get_zeitreihe_mw_by_id.sh", str(startYear), str(endYear), gpccId], cwd="/media/nas/x21971/GPCC_monthly")
  #p.wait()
  gpcc_filename = "mw_" + gpccId + ".dat"
  dat_path = "/media/nas/x21971/GPCC_monthly2/mw_" + gpccId + ".dat"
  zip_path = "/media/nas/x21971/GPCC_monthly2/mw_" + gpccId + ".zip"
  if not os.path.exists(zip_path):
    if not os.path.exists(dat_path):
      p = subprocess.Popen(["get_zeitreihe_mw_by_id.sh", str(startYear), str(endYear), gpccId], 
        cwd="/media/nas/x21971/GPCC_monthly2")
      p.wait()
      time.sleep(0.1)
      
      # Move retrieved .dat file to its own .zip folder
      with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.write(dat_path, arcname=gpcc_filename, compress_type=zipfile.ZIP_DEFLATED)
      time.sleep(0.1)
      os.remove(dat_path)
  
  #f = open("/media/nas/x21971/GPCC_monthly/mw_" + gpccId + ".dat", "r")
  zf = zipfile.ZipFile(zip_path, "r")
  f = zf.open(gpcc_filename, "r")
  f.readline()
  
  dates = []
  vals = []
  
  file_format_error = 0
  for line in f:
    lineList=line.rstrip().split()
    try:
      year = int(lineList[1])
      month = int(lineList[0])
      read_values = 1
    except:
      file_format_error = 1
      read_values = 0
      break
    if read_values == 1:
      day = calendar.monthrange(year, month)[1]
      #dates.append(datetime.date(int(lineList[1]), int(lineList[0]), 1))
      dates.append(datetime.date(year, month, day))
      gotOne = 0
      for v in lineList[2:]:
        if float(v) >= 0:
          vals.append(float(v))
          gotOne = 1
          break
      if gotOne==0:
        vals.append(np.nan)
  
  f.close()
  zf.close()
  
  #ddf = pd.DataFrame(vals, index=dates, columns=["GPCC"])
  
  if file_format_error == 0:
    ddf = pd.DataFrame(vals, index=dates, columns=["GPCC"])
    
    # Remove duplicate rows if present and check against unique index values
    ddf['date_tmp'] = ddf.index.copy()
    idx = ddf.index.drop_duplicates().copy()
    ddf.drop_duplicates(inplace=True)
    ddf.sort_index(inplace=True)
    ddf.drop(['date_tmp'], axis=1, inplace=True)
    #print(file_format_error, ddf.shape[0], idx.shape[0])
    if ddf.shape[0] != idx.shape[0]:
      vals = []
      dates = []
      ddf = pd.DataFrame(vals, index=dates, columns=["GPCC"])
      print(file_format_error, ddf.shape[0])
    
  elif file_format_error == 1:
    vals = []
    dates = []
    ddf = pd.DataFrame(vals, index=dates, columns=["GPCC"])
    print(file_format_error, ddf.shape[0])
  
  return ddf

# Match station id helper function ++++++++++++++++++++++++++++++++++++++++++++
def findIdenticalById(target, neighbour): #this probably needs refining...
  match = 0
  if target.station_id[3:] in neighbour.name:
    match = 1
  if target.station_id[3:] in neighbour.station_id:
    match = 1
  if target.station_id[3:] in neighbour.wmo_id:
    match = 1
  if target.original_station_number in neighbour.name:
    match = 1
  if target.original_station_number in neighbour.station_id:
    match = 1
  if target.original_station_number in neighbour.wmo_id:
    match = 1
  if target.original_station_name in neighbour.name:
    match = 1
  if target.original_station_name in neighbour.station_id:
    match = 1
  if target.original_station_name in neighbour.wmo_id:
    match = 1
    
  return match

"""
++++++++++++++++++++++++++++++++++ GPCC functions -end +++++++++++++++++++++++++++++++++++
"""

"""
++++++++++++++++++++++++++++++++++ Neighbour Checks +++++++++++++++++++++++++++++++++++
"""
def calculateAffinityIndexAndPearson(df1, df2): # done
  
  df = pd.concat([df1, df2], axis=1, join='inner')
  df.columns = ["ts1", "ts2"]
  df = df.dropna()
  
  # dp 29/11/2019 - extra check to make sure there is some overlap between
  # hourly target and neighbour - implemented because at least one error in
  # GPCC statlex_daily
  # - also possibility that dropping nans from df reduces overlap
  #if df.shape[0] > 0:
  if df.shape[0] >= 365:
    
    #p = 0.1 # 0.0 # 1.0
    a = np.around(df.loc[df['ts1'] >= 0.1, 'ts1'].min(), 1)
    b = np.around(df.loc[df['ts2'] >= 0.1, 'ts2'].min(), 1)
    p = max(a, b, 0.1)
    #print(a, b, p)
    #sys.exit()
    conditions = [
      (df['ts1'] >p) & (df['ts2']>p),
      (df['ts1'] ==p) & (df['ts2']==p),
      (df['ts1'] ==p) & (df['ts2']>p),
      (df['ts1'] >p) & (df['ts2']==p)]
    choices = [1, 1, 0, 0]
    
    df['duplicate'] = np.select(conditions, choices, default=np.nan)

    try:
        match = df["duplicate"].value_counts()[1]
    except:
        match = 0
    try:
        diff = df["duplicate"].value_counts()[0]
    except:
        diff = 0
    
    if (match > 0) or (diff > 0):
        perc = match/(match+diff)
        pCorr = df.ts1.corr(df.ts2)
        df["factor"] = df.ts1/df.ts2
        f = np.mean(df.loc[(df.ts1>0) & (df.ts2>0), "factor"].values)
    else:
        perc = 0
        pCorr = 0
        f = 0
    
    #print(df)
    #sys.exit()
  
  else:
    perc = 0
    pCorr = 0
    f = 0

  return perc, pCorr, f

# Main helper function, used by checkNeighbour and checkNeighbourDry
#def compareTargetToNeighbour(target, neighbour, highOrDry): #Liz check this still works with the ai>0.9 bit
def compareTargetToNeighbour(target, neighbour, highOrDry, station=None, check_type=None, neighbour_id=None): # last three args for output for normalised difference distribution checks
  
  ''' 
  After Upton and Rahimi 2003 https://www.sciencedirect.com/science/article/pii/S0022169403001422
  '''
  checkFlag = 1 # default setting is to check
  
  # dp 31/12/2019 - this AI check should be redundant, because neighbours with
  # AI < 0.9 are filtered out before this function is called...
  if highOrDry == "high":
    ai = calculateAffinityIndexAndPearson(target, neighbour)[0]
    if ai>=0.9:
      checkFlag = 1
    else:
      checkFlag = 0
  
  if checkFlag == 1:
    
    df = pd.concat([target, neighbour], axis=1, join='inner')
    df.columns = ["ts1", "ts2"]
    df = df.dropna()#.rename(columns={"GPCC":"ts2"})
    
    # There are cases where intermittent records cause problems in 15-days 
    # windows for dry spell stuff, so try to ensure reasonable overlap
    if df.shape[0] >= 365:
    
      # Separate out high and dry checks as using slightly different approaches now
      if highOrDry == "high":
        
        # Normalise target and neighbour series by their respective min/max and 
        # find differences
        # - retained actual amounts too to help filter on wet days
        #normalized_df = (df-df.min())/(df.max()-df.min())
        #normalized_df["norm_diff"] = normalized_df.ts1 - normalized_df.ts2
        df['ts1n'] = (df['ts1'] - df['ts1'].min()) / (df['ts1'].max() - df['ts1'].min())
        df['ts2n'] = (df['ts2'] - df['ts2'].min()) / (df['ts2'].max() - df['ts2'].min())
        df["nd"] = df['ts1n'] - df['ts2n']
        
        # ----------
        # *** dp 31/12/2019 - rather than using a median/standard deviation approach to
        # identifying outlying differences, switching to an approach based on presumption
        # that normalised differences for wet day amounts are roughly exponentially 
        # distributed (based on testing) - so commented out original approach below ***
        
        # Calculate robust estimates of location and scale
        #nm = float(normalized_df.norm_diff.median())
        #normalized_df["med_diff"] = ((normalized_df["norm_diff"] - nm)**2)**0.5
        #nM = normalized_df.med_diff.median()
        #sd = 1.4826*nM
        
        # Assign flags
        # dp (02/12/2019) 01m - testing percentile-based approach so commented 
        # out conditions - uncomment below to revert - copied in 03/12/2019 (01n2)
        #conditions = [
        #    (normalized_df['norm_diff'] <= 1.645*sd),
        #    (normalized_df['norm_diff'] > 1.645*sd)& (normalized_df['norm_diff'] <= 1.96*sd),
        #    (normalized_df['norm_diff'] > 1.96*sd)& (normalized_df['norm_diff'] <= 2.57*sd),
        #    (normalized_df['norm_diff'] > 2.57*sd)]
        #choices = [0, 1, 2, 3]
        # --
        #q90 = np.percentile(normalized_df['norm_diff'], 90.0)
        #q95 = np.percentile(normalized_df['norm_diff'], 95.0)
        #q99 = np.percentile(normalized_df['norm_diff'], 99.0)
        #conditions = [
        #    (normalized_df['norm_diff'] <= q90),
        #    (normalized_df['norm_diff'] > q90)& (normalized_df['norm_diff'] <= q95),
        #    (normalized_df['norm_diff'] > q95)& (normalized_df['norm_diff'] <= q99),
        #    (normalized_df['norm_diff'] > q99)]
        #choices = [0, 1, 2, 3]
        # --
        # ----------
        
        # Filter for target wet days, no NAs and positive differences
        df1 = df.loc[(df['ts1'] >= 1.0) & (np.isfinite(df['ts1'])) &
            (np.isfinite(df['ts2'])) & (df['nd'] > 0.0)]
        
        # Ensure still some data left to fit a distribution etc
        if df1.shape[0] >= 30:
          
          # Fit exponential distribution
          params = scipy.stats.expon.fit(df1['nd'])
          
          # Calculate thresholds at key percentiles of fitted distribution
          #q90 = scipy.stats.expon.ppf(0.90, params[0], params[1])
          q95 = scipy.stats.expon.ppf(0.95, params[0], params[1])
          q99 = scipy.stats.expon.ppf(0.99, params[0], params[1])
          q999 = scipy.stats.expon.ppf(0.999, params[0], params[1])
          
          # Assign flags
          # - no need for an additional condition that target exceeds wet day threshold
          # because the percentiles are defined based on just positive differences?
          # -- left in for now...
          conditions = [
              (df['ts1'] >= 1.0) & (df['nd'] <= q95),
              (df['ts1'] >= 1.0) & (df['nd'] > q95) & (df['nd'] <= q99),
              (df['ts1'] >= 1.0) & (df['nd'] > q99) & (df['nd'] <= q999),
              (df['ts1'] >= 1.0) & (df['nd'] > q999)]
          choices = [0, 1, 2, 3]
          
          # *** dp 27/11/2019 *** - commented out line below so changed to default=0
          #normalized_df['temp_flags'] = np.select(conditions, choices, default=np.nan)
          ##normalized_df['temp_flags'] = np.select(conditions, choices, default=0)
          df['temp_flags'] = np.select(conditions, choices, default=0)
          
          '''
          # ---
          # Outputs for checking thresholds for neighbour check flags
          if highOrDry == "high":
          
              # For looking at distributions of normalised differences
              nvals = normalized_df['norm_diff'].count()
              lower_bounds = [-1.0, -0.5, -0.3, -0.2, -0.1, -0.05, -0.01, 0.01, 0.05, 0.1, 0.2, 0.3, 0.5]
              upper_bounds = [-0.5, -0.3, -0.2, -0.1, -0.05, -0.01, 0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0]
              #np.around(np.arange(-1.0, 1.0, 0.05), 2)
              for lb, ub in zip(lower_bounds, upper_bounds):
                  # c = normalized_df.loc[(normalized_df['norm_diff'] >= lb) &
                      # (normalized_df['norm_diff'] < (lb + 0.05)), 'norm_diff'].count()
                  c = normalized_df.loc[(normalized_df['norm_diff'] >= lb) &
                      (normalized_df['norm_diff'] < ub), 'norm_diff'].count()
                  if nvals > 0:
                      freq = c/float(nvals)
                  else:
                      freq = 0.0
                  mb = np.around(((ub-lb)/2.0) + lb, 3)
                  output_list = [station, check_type, neighbour_id, mb, freq]
                  output_line = ",".join(str(v) for v in output_list)
                  fh_dist.write(output_line + "\n")
              
              # For looking at key potential thresholds
              qi = {}
              for i in [95,99,99.9,99.99]:
                  try:
                      qi[i] = np.percentile(normalized_df['norm_diff'], i)
                  except:
                      qi[i] = 'NA'
              dc = {
                  'Median': nm,
                  'Median_MAD': nM,
                  'SD_MedMAD': sd,
                  'SD': np.std(normalized_df['norm_diff']),
                  'Q95': qi[95],
                  'Q99': qi[99],
                  'Q99pt9': qi[99.9],
                  'Q99pt99': qi[99.99]}
              stats_list = ['Median', 'Median_MAD', 'SD_MedMAD', 'SD', 
                  'Q99', 'Q99pt9', 'Q99pt99']
              #for k,v in dc.items():
              for k in stats_list:
                  v = dc[k]
                  output_list = [station, check_type, neighbour_id, k, v]
                  output_line = ",".join(str(v) for v in output_list)
                  fh_thrsh.write(output_line + "\n")
          # ---
          '''
          
          #tempFlags = normalized_df['temp_flags']
          tempFlags = df['temp_flags']
          return tempFlags
        
        else:
          return pd.Series([])
      
      elif highOrDry == "dry":
        
        # Assign flags
        # - consider only whether dry 15-day periods at the target are 
        # corroborated as dry by neighbours
        # - check based on whether 0, 1, 2 or >= 3 wet days are recorded at the
        # neighbour when the target is dry over the 15-day period
        # - dry flag works on the basis of fraction of dry days within 15-day
        # moving window, so 1 = all dry, 0 = all wet
        # -- truncating these fractions to 2 dp below and manipulating equalities
        # to work with these fractions, but could work in days not fractions if
        # change the convertToDrySpell function
        # - in dry day fraction calcs a threshold of 0 mm is currently used to
        # identify days as wet (i.e. any rainfall)
        frac_drydays = {}
        for d in range(1, 3+1):
            frac_drydays[d] = np.trunc((1.0 - (float(d) / 15.0)) * 10 ** 2) / (10 ** 2)
        conditions = [
            (df['ts1'] == 1.0) & (df['ts2'] == 1.0),
            (df['ts1'] == 1.0) & (df['ts2'] < 1.0) & (df['ts2'] >= frac_drydays[1]),
            (df['ts1'] == 1.0) & (df['ts2'] < frac_drydays[1]) & (df['ts2'] >= frac_drydays[2]),
            (df['ts1'] == 1.0) & (df['ts2'] < frac_drydays[2])] #  & (df['ts2'] >= frac_drydays[3])
        choices = [0, 1, 2, 3]
        
        # *** dp 27/11/2019 *** - commented out line below so changed to default=0
        #normalized_df['temp_flags'] = np.select(conditions, choices, default=np.nan)
        ##normalized_df['temp_flags'] = np.select(conditions, choices, default=0)
        df['temp_flags'] = np.select(conditions, choices, default=0)
        
        #tempFlags = normalized_df['temp_flags']
        tempFlags = df['temp_flags']
        return tempFlags
      
    else:
      return pd.Series([])
    
  else:
    return pd.Series([])

# Monthly checks ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
def compareTargetToNeighbourMonthly(target, neighbour):
  
  df = pd.concat([target, neighbour], axis=1, join='inner')
  
  df = df.dropna().rename(columns={"GPCC":"ts2", "target":"ts1"})

  #df["perc_diff"]=(df.ts1 - df.ts2)%100/df.ts2
  df["perc_diff"]=(df.ts1 - df.ts2)*100./df.ts2
  df["factor_diff"]=df.ts1/df.ts2
  # conditions = [
    # (df['perc_diff'] < -20),
    # (df['perc_diff'] < -10)& (df['perc_diff'] >= -20),
    # (df['perc_diff'] < -5)& (df['perc_diff'] >= -10),
    # (df['perc_diff'] <= 5)& (df['perc_diff'] >= -5),
    # (df['perc_diff'] > 5)& (df['perc_diff'] <= 10),
    # (df['perc_diff'] > 10)& (df['perc_diff'] <= 20),
    # (df['perc_diff'] > 20)]
  conditions = [
    (df['perc_diff'] <= -100.0),
    (df['perc_diff'] <= -50.0) & (df['perc_diff'] > -100.0),
    (df['perc_diff'] <= -25.0) & (df['perc_diff'] > -50),
    (df['perc_diff'] < 25.0) & (df['perc_diff'] > -25.0),
    (df['perc_diff'] >= 25.0) & (df['perc_diff'] < 50.0),
    (df['perc_diff'] >= 50.0) & (df['perc_diff'] < 100.0),
    (df['perc_diff'] >= 100.0)]

  choices = [-3,-2,-1,0, 1, 2, 3]
  
  df['temp_flags'] = np.select(conditions, choices, default=np.nan)
  
  df.loc[np.isnan(df['ts1']), 'temp_flags'] = np.nan
  tempFlags = df['temp_flags']

  conditionsF = [
    (df['factor_diff'] < 11)& (df['factor_diff'] >9), # hourly is approx 10x greater than monthly
    (df['factor_diff'] < 26)& (df['factor_diff'] >24), # hourly is approx 25.4x greater than monthly
    (df['factor_diff'] < 3)& (df['factor_diff'] >2), # hourly is approx 2.45x greater than monthly
    (df['factor_diff'] > 1/11)& (df['factor_diff'] <1/9),
    (df['factor_diff'] > 1/26)& (df['factor_diff'] <1/24),
    (df['factor_diff'] > 1/3)& (df['factor_diff'] <1/2)]

  choicesF = [1,2,3,4,5,6]
  
  #df['factor_flags'] = np.select(conditionsF, choicesF, default=np.nan)
  df['factor_flags'] = np.select(conditionsF, choicesF, default=0)
  
  df.loc[np.isnan(df['ts1']), 'factor_flags'] = np.nan
  factorFlags = df['factor_flags']
  
  #print(tempFlags.isnull().any(), factorFlags.isnull().any())
  #print(tempFlags.max(), factorFlags.max())
  
  return [tempFlags, factorFlags] # Hey Liz! Make sure theres a new thing for factor flags!

 #++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# Uses compareTargetToNeighbour function based on Upton and Rahimi(2003) 
#def checkNeighbours(target, neighbours):
def checkNeighbours(target, neighbours, station=None, check_type=None): # temporary extra args for checking neighbour stuff

  df = target
  concatList = [df]
  
  nid = 1 # temporary for neighbours checking
  for n in neighbours:
    dfn = compareTargetToNeighbour(df, n, "high", station, check_type, nid)
    if dfn.empty:
      pass
    else:
      concatList.append(dfn)
    nid += 1
  df = pd.concat(concatList, axis=1, join='outer')

  cols = ["n" + str(i+1) for i in range(len(concatList)-1)]
  cols2 = ["target"]
  cols2.extend(cols)

  df.columns = cols2
  
  # dp 28/11/2019 - changed assuming that looking for number of neighbours online
  # on any given day (similar to monthly neighbours)
  ##df["online"] = len(concatList)-1 - df[cols].isnull().T.sum(axis=1)
  df["online"] = len(concatList) - df[cols].isnull().sum(axis=1) - 1
  
  #print(df)
  #print(df['online'].min(), df['online'].median())
  #for i in range(0, 5+1):
  #  c = df.loc[df['online'] == i, 'online'].count()
  #  print(i, c, c / float(len(df['online'])))
  #sys.exit()
  
  conditions = [
    ((df[cols] == 3).T.sum() == df.online),
    ((df[cols] >= 2).T.sum() == df.online),
    ((df[cols] >= 1).T.sum() == df.online)]

  choices = [3,2,1]
  df["flags"] = np.select(conditions, choices, default=0)
  
  #print(df)
  #for i in range(0, 3+1):
  #  c = df.loc[df['flags'] == i, 'flags'].count()
  #  print(i, c, c / float(len(df['flags'])))
  #sys.exit()
  
  df.loc[df.online < 3, "flags"] = np.nan
  dfr = df.flags
  
  #print()
  #for i in range(0, 3+1):
  #  c = df.loc[df['flags'] == i, 'flags'].count()
  #  print(i, c, c / float(len(df['flags'])))
  #print(df.loc[df['flags'].isnull()])
  #print(df.loc[df['online'] == 1])
  #print(df.loc[df['flags'] == 1])
  #print(df.loc[df['flags'] == 2])
  #print(df.loc[df['flags'] == 3])
  #sys.exit()
  
  return dfr

#++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# Uses compareTargetToNeighbour function based on Upton and Rahimi(2003)
def checkNeighboursDry(target, neighbours): #Liz check this
  
  df = convertToDrySpell(target) #Liz, check column names
  
  #print(df.max(), df.mean(), df.median())
  #tmp = np.asarray(df)
  #tmp = tmp[np.isfinite(tmp)]
  #print(np.unique(tmp, return_counts=True))
  #print(tmp.shape)
  #tmp = df.to_frame('val')
  #print(tmp.loc[tmp['val'] == 1])
  #sys.exit()
  
  concatList = [df]
  for n in neighbours:
    nn = convertToDrySpell(n) #Liz check column names
    dfn = compareTargetToNeighbour(df, nn, "dry")
    if dfn.empty:
      pass
    else:
      concatList.append(dfn)
    
  df = pd.concat(concatList, axis=1, join='outer')

  cols = ["n" + str(i+1) for i in range(len(concatList)-1)]
  cols2 = ["target"]
  cols2.extend(cols)
  df.columns = cols2
  
  # dp 28/11/2019 - again assumed want count of number of neighbours online
  #df["online"] = len(concatList) -1 - df.isnull().sum(axis=1)
  df["online"] = len(concatList) - df[cols].isnull().sum(axis=1) - 1
  
  df["flags"] = np.floor(df[cols].sum(axis=1)/df.online)
  
  #print(df)
  #print(df.loc[df['flags'] == df['flags'].max()])
  #print(df['flags'].max())
  #print(df.loc[df['flags'] == df['flags'].min()])
  #print(df['flags'].min())
  #print(df.loc[df['flags'] == 1])
  #print(df.loc[df['target'] == 1])
  #tmp = np.asarray(df.loc[np.isfinite(df['flags']), 'flags'])
  #print(np.unique(tmp, return_counts=True))
  #sys.exit()
  
  # *** dp 29/11/2019 - in last section below, why set flags to -999 when target equals 0? in this
  # case target is between 0 and 1 because it is the fraction of dry days in the 15-day period
  # ending on each date. 1 means all dry days in the period, so it should be values less than
  # 1 that are set to -999 i think - changed to that effect for now... ***
  # - may also be better to give a different flag than nan to show that the check has been done and
  #   not failed e.g. due to lack of neighbours?
  
  # *** dp 01/01/2020 - changed so that no adjustment to flags where period is 
  # not totally dry (i.e. df.target < 1) on the basis that this is now handled in
  # the revised dry neighbours check (so just commented out line below)
  
  df.flags = df.flags.replace([np.inf, -np.inf], -999)
  df.loc[df.online < 3, "flags"] = -999
  #df.loc[df.target > 0, "flags"] = -999
  ##df.loc[df.target < 1, "flags"] = -999
  df.flags = df.flags.astype(int)
  df.flags = df.flags.replace(-999, np.nan)
  dfr = df.flags
  
  return dfr

#++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
def checkMNeighbours(target, neighbours):
  
  df = target
  concatList = [df]
  ticker = 0
  for n in neighbours:
    dfn, dff = compareTargetToNeighbourMonthly(df, n)
    concatList.append(dfn)
    if ticker == 0:
      dfFactor = dff.copy()
    ticker +=1

  df = pd.concat(concatList, axis=1, join='outer')

  cols = ["n" + str(i+1) for i in range(len(concatList)-1)]
  cols2 = ["target"]
  cols2.extend(cols)

  df.columns = cols2
  
  #df["online"] = len(concatList) -1 - df[cols].isnull().T.sum(axis=1)
  df["online"] = len(concatList) - df[cols].isnull().sum(axis=1) - 1
  conditions = [
    ((df[cols] == -3).T.sum() == df.online),
    ((df[cols] <= -2).T.sum() == df.online),
    ((df[cols] <= -1).T.sum() == df.online),
    ((df[cols] == 3).T.sum() == df.online),
    ((df[cols] >= 2).T.sum() == df.online),
    ((df[cols] >= 1).T.sum() == df.online)]

  choices = [-3,-2,-1,3, 2, 1]
  
  df["flags"] = np.select(conditions, choices, default=0)
  df.loc[np.isnan(df['target']), 'flags'] = np.nan
  
  # Add additional checks in relation to monthly neighbours climatology
  
  # Calculate neighbour monthly climatology (monthly min/max across all neighbours)
  tmp = []
  for n in neighbours:
    tmp.append(n)
  df_mc = pd.concat(tmp, axis=1, join='outer')
  cols3 = ["n" + str(i+1) for i in range(len(tmp))]
  df_mc.columns = cols3
  df_mc.index = pd.to_datetime(df_mc.index, format='%Y-%m-%d')
  df_mc_max = df_mc.groupby(df_mc.index.month).max()
  df_mc_max['max'] = df_mc_max.max(axis=1)
  df_mc_min = df_mc.groupby(df_mc.index.month).min()
  df_mc_min['min'] = df_mc_min.min(axis=1)
  df_mc2 = pd.concat(
    [df_mc_min.drop(cols3, axis=1), df_mc_max.drop(cols3, axis=1)], axis=1,
    join='outer')
  df_mc2['month'] = df_mc2.index
  
  # Join monthly climatology to target series
  df['month'] = df.index.month
  df['date'] = df.index
  df2 = df.merge(df_mc2, left_on='month', right_on='month')
  df2.set_index('date', inplace=True)
  df2.sort_index(inplace=True)
  
  # Adjust flag where -3 or 3 and rainfall is outside neighbours min/max range
  df2.loc[(df2['flags'] == -3) & (df2['online'] >= 3) &
    ((df2['target'] < (0.75 * df2['min'])) | (df2['target'] > (1.25 * df2['max']))), 
    'flags'] = -4
  df2.loc[(df2['flags'] == 3) & (df2['online'] >= 3) &
    ((df2['target'] < (0.75 * df2['min'])) | (df2['target'] > (1.25 * df2['max']))), 
    'flags'] = 4
  
  # If target monthly total rainfall is < 50 mm then change flag
  # - not used currently
  # df2.loc[((df2['flags'] >= 3) | (df2['flags'] <= -3)) & 
    # (df2['target'] < 50.0)] = 6
  
  # Flag where less than 3 neighbours but value > 2 * neighbour max or
  # < 0.5 * neighbour min
  df2.loc[(df2['online'] < 3) & (df2['target'] > (2.0 * df2['max'])), 
    'flags'] = 5
  df2.loc[(df2['online'] < 3) & (df2['target'] < (0.5 * df2['min'])), 
    'flags'] = -5
  
  #print(df2.loc[df2.index.year==1987])
  #print(df2.loc[df2.index.year==2003])
  #print(df2.loc[df2['flags']==-4])
  
  # If less than 3 stations online than flag check as incomplete unless flag
  #has a value of -5/5 (i.e. outside climatology range)
  #df.loc[df.online < 3, "flags"] = np.nan
  #dfr = df.flags
  df2.loc[(df2.online < 3) & (np.absolute(df2['flags']) != 5), "flags"] = np.nan
  dfr = df2.flags
  
  #print(df2.loc[df2['flags']==-4])
  #sys.exit()
  
  #print(df2)
  #print(np.unique(df2['flags'][np.isfinite(df2['flags'])], return_counts=True))
  #print(df2.loc[(df2['flags'] == -4) | (df2['flags'] == 3) | (df2['flags'] == 4)])
  #print(df_mc.loc[df_mc.index.year==1992])
  #sys.exit()
  
  #print(df)
  #print(df.loc[df.index.year == 2003])
  #print(len(neighbours))
  #sys.exit()
  
  return [dfr, dfFactor]

#++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
def checkHourlyNeighbours(target):
  
  df = target.data.to_frame("target")
  
  # convert hourly to daily 7am-7am
  #print("converting to daily")
  #df["roll"] = np.around(df.target.rolling(window=24, center=False, min_periods=24).sum())
  df["roll"] = np.around(df.target.rolling(window=24, center=False, min_periods=24).sum(), 1)
  dfd = df[df.index.hour==7]
  dts = list(dfd.index)
  dailyVals = list(dfd.roll)
  
  #print(df.loc[df['target'] >= 0.1, 'target'].min())
  #print(df.loc[df['roll'] >= 0.1, 'roll'].min())
  #sys.exit()

  dts0 = []
  for dt in dts:
    s0  = dt -datetime.timedelta(days=1)
    dts0.append(datetime.date(s0.year, s0.month, s0.day))
  ts0 = pd.Series(dailyVals, index=dts0)
  
  #print(df)
  #print(dfd)
  #print(ts0)
  #print(ts0.min())
  #sys.exit()

  # find neighbours
  #print("finding neighbours")
  neighbours, paths = findHourlyNeighbours(target)
  
  # dp 30/11/2019 - assuming neighbours[0] is the target
  if len(neighbours) > 1:
    
    # ---
    # *** dp 30/11/2019 temporarily remove any ISD neighbours just for neighbour difference distribution checking ***
    '''
    #if len(neighbours) > 0:
    tmp = []
    tmp2 = []
    for n,p in zip(neighbours, paths):
      if n.startswith('DE_'):
        tmp.append(n)
        tmp2.append(p)
    neighbours = tmp
    paths = tmp2
    '''
    # ---
    
    """
    More GSDR bits here Liz: -+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-
    """
    # get GSDR 
    #print("gettingGSDR")
    neighbourDfs = []
    for nId in range(len(neighbours)):
      if nId == 0:
        pass
      else:
        neighbourDfs.append(getGSDR(neighbours[nId], paths[nId]))
    # get matching stats for nearest gauge and offset calculateAffinityIndexAndPearson(ts1, ts2) -> returns a flag
    
    # do neighbour check 
    #print("doing neighbour check")
    #for nb in neighbours:
    #  print(nb)
    
    #filter out gauges with AI < 0.9
    neighbourDfs2 = []
    for ndf in neighbourDfs:
      nAI, nR2, nf = calculateAffinityIndexAndPearson(ts0.to_frame("ts1"), ndf)
      #nId = neighbourDfs[neighbourDfs.index(ndf)]
      #print(nAI, nR2, nf, ndf.shape[0])
      if nAI > 0.9:
        neighbourDfs2.append(ndf)
      else:
        pass
    
    flagsDf = checkNeighbours(ts0.to_frame("ts1"), neighbourDfs2, target.station_id, 'hourly')
    
    # ---
    # dp (11/12/2019) - temporary to save normalised differences and examine fit to some distributions
    '''
    df1 = ts0.to_frame("ts1")
    concat_list = [df1['ts1']]
    
    for n in neighbourDfs2:
      df2 = pd.concat([df1, n], axis=1, join='inner')
      df2.columns = ["ts1", "ts2"]
      df2 = df2.dropna()
      if df2.shape[0] >= 365:
        df_nd = (df2 - df2.min()) / (df2.max() - df2.min())
        df_nd["norm_diff"] = df_nd.ts1 - df_nd.ts2
        concat_list.append(df_nd['norm_diff'])
    
    df3 = pd.concat(concat_list, axis=1, join='outer')
    cols = ['val']
    cols2 = ["n" + str(i+1) for i in range(len(neighbourDfs2))] # concat_list
    cols.extend(cols2)
    df3.columns = cols
    
    output_path = nd_folder + target.station_id + "_H01.csv"
    df3.to_csv(output_path, na_rep='NA', index_label='Date')
    '''
    # ---
    
    flagsDates= list(flagsDf.index.values)
    flagsVals=list(flagsDf)
    
    #tmp = flagsDf.to_frame('val')
    #print(tmp.loc[tmp['val'] > 0])
    #sys.exit()
    
    #print(flagsDf[np.isfinite(flagsDf)])
    #sys.exit()
    
    # do neighbour check for dry periods and flag the whole 15 day period
    dryFlagsDf = checkNeighboursDry(ts0.to_frame("ts1"), neighbourDfs2)
    dryFlagsDates= list(dryFlagsDf.index.values)
    dryFlagsVals=list(dryFlagsDf)  
    
    #tmp = dryFlagsDf.to_frame('vals')
    #print(tmp.loc[tmp['vals']==0])
    #print(dfd.shape)
    #print(flagsDf.shape)
    #print(dryFlagsDf.shape)
    #print(ts0.shape)
    #sys.exit()
    
    i1 = []
    i2 = []
    i3 = []
    
    for i in range(len(dryFlagsVals)):
      if dryFlagsVals[i] == 1:
        for j in range(15):
          i1.append(i-j)
      elif dryFlagsVals[i] == 2:
        for j in range(15):
          i2.append(i-j)
      elif dryFlagsVals[i] == 3:
        for j in range(15):
          i3.append(i-j)
      else:
        pass
        
    for i in i1:
      dryFlagsVals[i] = 1
    for i in i2:
      dryFlagsVals[i] = 2
    for i in i3:
      dryFlagsVals[i] = 3
      
    # add daily flags back onto hourly
    flagsDt=[datetime.datetime(d.year, d.month, d.day, 7) for d in flagsDates]
    flagsDf = pd.Series(flagsVals, index=flagsDt).to_frame("flags")
    dryFlagsDt=[datetime.datetime(d.year, d.month, d.day, 7) for d in dryFlagsDates]
    dryFlagsDf = pd.Series(dryFlagsVals, index=dryFlagsDt).to_frame("dryFlags")  
    
    df = pd.concat([df, flagsDf, dryFlagsDf], axis=1, join_axes=[df.index])
    df.flags = df.flags.fillna(method="ffill", limit=23)
    df.dryFlags = df.dryFlags.fillna(method="ffill", limit=23)
    df.fillna(-999, inplace=True)
    
    return [list(df.flags.astype(int)), list(df.dryFlags.astype(int))]
  
  # -999 if no neighbours
  else:
    tmp = [-999 for i in range(df['roll'].shape[0])]
    return [tmp, tmp]

#++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
def checkDailyNeighbours(target):
  
  df = target.data.to_frame("target")
  # convert hourly to daily 7am-7am
  #print("converting to daily")
  #df["roll"] = np.around(df.target.rolling(window=24, center=False, min_periods=24).sum())
  df["roll"] = np.around(df.target.rolling(window=24, center=False, min_periods=24).sum(), 1)
  dfd = df[df.index.hour==7]
  dts = list(dfd.index)
  dailyVals = list(dfd.roll)
  
  # offset by one day in either direction
  dtsm1 = []
  dts0 = []
  dtsp1 = []
  for dt in dts:
    sm1 = dt -datetime.timedelta(days=2)
    s0  = dt -datetime.timedelta(days=1)
    sp1 = dt

    dtsm1.append(datetime.date(sm1.year, sm1.month, sm1.day))
    dts0.append(datetime.date(s0.year, s0.month, s0.day))
    dtsp1.append(datetime.date(sp1.year, sp1.month, sp1.day))
 
  tsm1 = pd.Series(dailyVals, index=dtsm1)
  ts0 = pd.Series(dailyVals, index=dts0)
  tsp1 = pd.Series(dailyVals, index=dtsp1)
  
  # find neighbours
  #print("finding neighbours")
  neighbours = findDailyNeighbours(target)
  
  # Check for duplicate neighbours
  if len(neighbours) > 0:
    tmp = []
    for n in neighbours:
      if n not in tmp:
        tmp.append(n)
    neighbours = tmp.copy()
  
  # dp 30/11/2019
  if len(neighbours) > 0:
  
    # get gpcc 
    #print("gettingGPCC")
    neighbourDfs = []
    for nId in neighbours:
      #neighbourDfs.append(getGPCC(dts0[0].year, dts0[-1].year, nId))
      neighbour_start_year = dailyDates[dailyNames.index(nId)][0].year
      neighbour_end_year = dailyDates[dailyNames.index(nId)][1].year
      neighbourDfs.append(getGPCC(neighbour_start_year, neighbour_end_year, nId))
    
    # get matching stats for nearest gauge and offset calculateAffinityIndexAndPearson(ts1, ts2) -> returns a flag
    nearest = neighbourDfs[0].rename(columns={"GPCC":"ts2"})
    sm1AI, sm1R2, sm1f = calculateAffinityIndexAndPearson(tsm1.to_frame("ts1"), nearest)
    s0AI, s0R2, s0f = calculateAffinityIndexAndPearson(ts0.to_frame("ts1"), nearest)
    sp1AI, sp1R2, sp1f = calculateAffinityIndexAndPearson(tsp1.to_frame("ts1"), nearest)
    
    ais = [sm1AI, s0AI, sp1AI]
    r2s = [sm1R2, s0R2, sp1R2]
    
    #print(ais)
    #print(r2s)
    #sys.exit()
    
    if ais.index(max(ais)) == r2s.index(max(r2s)):
      offset_flag = ais.index(max(ais)) -1
    else:
      offset_flag = 0
      
    # do neighbour check 
    #print("doing neighbour check")
    
    # dp 29/11/2019 - check that there is indeed some overlap between the hourly and GPCC
    # daily gauge - for DE_02483 one neighbour (3798) ends in 1972 in the data file but
    # statlex_daily says it continues until 2018, which results in no overlap and
    # a divide by zero error when trying to calculate the percentage matching
    # - for now check placed in calculate AI etc function
    
    #filter out gauges with AI < 0.9
    neighbourDfs2 = []
    for nId, ndf in zip(neighbours, neighbourDfs):
      ndf2 = ndf.rename(columns={"GPCC":"ts2"})
      #print(nId)
      #print(ndf2)
      nAI, nR2, nf = calculateAffinityIndexAndPearson(ts0.to_frame("ts1"), ndf2)
      #print(nId, nAI, nR2, nf)
      if nAI > 0.9:
        neighbourDfs2.append(ndf)
      else:
        pass
    
    #flagsDf = checkNeighbours(ts0.to_frame("ts1"), neighbourDfs, target.station_id, 'daily')
    flagsDf = checkNeighbours(ts0.to_frame("ts1"), neighbourDfs2, target.station_id, 'daily')
    flagsDates = list(flagsDf.index.values)
    flagsVals =list(flagsDf)
    
    # ---
    # dp (11/12/2019) - temporary to save normalised differences and examine fit to some distributions
    '''
    df1 = ts0.to_frame("ts1")
    concat_list = [df1['ts1']]
    
    for n in neighbourDfs2:
      df2 = pd.concat([df1, n], axis=1, join='inner')
      df2.columns = ["ts1", "ts2"]
      df2 = df2.dropna()
      if df2.shape[0] >= 365:
        df_nd = (df2 - df2.min()) / (df2.max() - df2.min())
        df_nd["norm_diff"] = df_nd.ts1 - df_nd.ts2
        concat_list.append(df_nd['norm_diff'])
    
    df3 = pd.concat(concat_list, axis=1, join='outer')
    cols = ['val']
    cols2 = ["n" + str(i+1) for i in range(len(neighbourDfs2))]
    cols.extend(cols2)
    df3.columns = cols
    
    output_path = nd_folder + target.station_id + "_D01.csv"
    df3.to_csv(output_path, na_rep='NA', index_label='Date')
    '''
    # ---
    
    #tmp = flagsDf.to_frame('val')
    #print(tmp.loc[tmp['val'] > 0])
    #sys.exit()
    
    # do neighbour check for dry periods and flag the whole 15 day period
    #dryFlagsDf = checkNeighboursDry(ts0.to_frame("ts1"), neighbourDfs)
    dryFlagsDf = checkNeighboursDry(ts0.to_frame("ts1"), neighbourDfs2)
    dryFlagsDates = list(dryFlagsDf.index.values)
    dryFlagsVals =list(dryFlagsDf)  
    
    i1 = []
    i2 = []
    i3 = []
    
    for i in range(len(dryFlagsVals)):
      if dryFlagsVals[i] == 1:
        for j in range(15):
          i1.append(i-j)
      elif dryFlagsVals[i] == 2:
        for j in range(15):
          i2.append(i-j)
      elif dryFlagsVals[i] == 3:
        for j in range(15):
          i3.append(i-j)
      else:
        pass
        
    for i in i1:
      dryFlagsVals[i] = 1
    for i in i2:
      dryFlagsVals[i] = 2
    for i in i3:
      dryFlagsVals[i] = 3
        
      
    # add daily flags back onto hourly
    flagsDt=[datetime.datetime(d.year, d.month, d.day, 7) for d in flagsDates]
    flagsDf = pd.Series(flagsVals, index=flagsDt).to_frame("flags")
    dryFlagsDt=[datetime.datetime(d.year, d.month, d.day, 7) for d in dryFlagsDates]
    dryFlagsDf = pd.Series(dryFlagsVals, index=dryFlagsDt).to_frame("dryFlags")  
    
    df = pd.concat([df, flagsDf, dryFlagsDf], axis=1, join_axes=[df.index])
    df.flags = df.flags.fillna(method="ffill", limit=23)
    df.dryFlags = df.dryFlags.fillna(method="ffill", limit=23)
    df.fillna(-999, inplace=True)
    return [list(df.flags.astype(int)), offset_flag, s0AI, s0R2, s0f, list(df.dryFlags.astype(int))]
  
  # -999 if no neighbours
  else:
    tmp = [-999 for i in range(df['roll'].shape[0])]
    return [tmp, -999, -999, -999, -999, tmp]

#++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
def convertToDrySpell(dailyDf):
  
  # dp 29/11/2019 - it would make sense to remove np.around so fractional not binary,
  # but this will require a change to how the flagging is done for it to make sense
  # i think (i.e. do all stations agree the period is wet when the target is dry?)
  # should the threshold for dry be larger than just zero?
  
  #dailyDf["fracDryDays"] = np.around(dailyDf.rolling(15, min_periods=15).apply(lambda window: (window == 0).sum()/15))
  dailyDf["fracDryDays"] = dailyDf.rolling(15, min_periods=15).apply(lambda window: (window == 0).sum()/15)
  
  convertedDf = dailyDf["fracDryDays"]
  convertedDf.columns = ["ts1"]
  return  convertedDf

#++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
def checkMonthlyNeighbours(target): #Hey Liz! check this when you have access to monthly, esp mapping back onto hourly.
  df = target.data.to_frame("target")
  
  # convert hourly to daily 7am-7am
  #print("converting to monthly")
  ##dfm = df.resample('M').sum()
  dfm = df.resample("M", label='right', closed='right').apply(lambda x: x.values.sum())
  #dts0 = [datetime.datetime.utcfromtimestamp(x.astype(datetime.datetime)) for x in list(dfm.index.values)]

  startYear = datetime.datetime.strptime(str(dfm.iloc[0].name), "%Y-%m-%d %H:%M:%S").year
  endYear = datetime.datetime.strptime(str(dfm.iloc[-1].name), "%Y-%m-%d %H:%M:%S").year
  # find neighbours
  #print("finding neighbours")
  neighbours = findMonthlyNeighbours(target)
  
  # Check for duplicate neighbours
  if neighbours is not None:
    tmp = []
    for n in neighbours:
      if n not in tmp:
        tmp.append(n)
    neighbours = tmp.copy()
  
  if neighbours is None:
    hourlyFlagsS = df.copy()
    hourlyFlagsS['flags'] = -999
    hourlyFactorFlagsS = df.copy()
    hourlyFactorFlagsS['factor_flags'] = -999
  else:
  
    # get gpcc 
    #print("gettingGPCC")
    neighbourDfs = []
    for nId in neighbours:
      #neighbourDfs.append(getMonthlyGPCC(startYear, endYear, nId))
      neighbour_start_year = monthlyDates[monthlyNames.index(nId)][0].year
      neighbour_end_year = monthlyDates[monthlyNames.index(nId)][1].year
      neighbourDfs.append(getMonthlyGPCC(neighbour_start_year, neighbour_end_year, nId))
    # get matching stats for nearest gauge and offset calculateAffinityIndexAndPearson(ts1, ts2) -> returns a flag

    # do neighbour check 
    #print("doing neighbour check")
    #print(neighbours)

    flagsDf, factorFlagsDf = checkMNeighbours(dfm, neighbourDfs)
    
    # set dates to be at 2300 (rather than 0000) so bfill works
    flagsDf.index += datetime.timedelta(hours=23)
    factorFlagsDf.index += datetime.timedelta(hours=23)

    origDates= list(df.index.values)
    hourlyFlagsS = flagsDf.reindex(origDates, method="bfill")
    hourlyFactorFlagsS = factorFlagsDf.reindex(origDates, method="bfill")
    
    # count valid values within month and set flag as nan if more than 5% of data is missing
    # - hourly percentage differences
    hourlyFlagsS = hourlyFlagsS.to_frame()
    hourlyFlagsS['count'] = hourlyFlagsS.groupby([hourlyFlagsS.index.year, hourlyFlagsS.index.month]).transform('count')
    hourlyFlagsS['expected'] = hourlyFlagsS.index.days_in_month * 24
    hourlyFlagsS['frac_complete'] = hourlyFlagsS['count'] / hourlyFlagsS['expected']
    hourlyFlagsS.loc[hourlyFlagsS['frac_complete'] < 0.95, 'flags'] = np.nan
    hourlyFlagsS.drop(['count', 'expected', 'frac_complete'], axis=1, inplace=True)
    # - hourly factor differences
    #print(hourlyFactorFlagsS)
    hourlyFactorFlagsS = hourlyFactorFlagsS.to_frame()
    hourlyFactorFlagsS['count'] = hourlyFactorFlagsS.groupby([hourlyFactorFlagsS.index.year, hourlyFactorFlagsS.index.month]).transform('count')
    hourlyFactorFlagsS['expected'] = hourlyFactorFlagsS.index.days_in_month * 24
    hourlyFactorFlagsS['frac_complete'] = hourlyFactorFlagsS['count'] / hourlyFactorFlagsS['expected']
    hourlyFactorFlagsS.loc[hourlyFactorFlagsS['frac_complete'] < 0.95, 'factor_flags'] = np.nan
    hourlyFactorFlagsS.drop(['count', 'expected', 'frac_complete'], axis=1, inplace=True)
    #print(hourlyFactorFlagsS)
    #print(hourlyFactorFlagsS.loc[hourlyFactorFlagsS['factor_flags'].notnull()])
    #print(hourlyFactorFlagsS['factor_flags'].min(), hourlyFactorFlagsS['factor_flags'].max())
    
    hourlyFlagsS.fillna(-999, inplace=True)
    hourlyFactorFlagsS.fillna(-999, inplace=True)
    
    #print(np.unique(hourlyFlagsS.loc[hourlyFlagsS['flags'].notnull(), 'flags'], return_counts=True))
    #sys.exit()
  
  #return [list(hourlyFlagsS.astype(int)), list(hourlyFactorFlagsS.astype(int))]
  return [list(hourlyFlagsS['flags'].astype(int)), list(hourlyFactorFlagsS['factor_flags'].astype(int))]

"""
+++++++++++++++++++++++++++++++ MAIN FUNCTION, CALLS CHECKS ++++++++++++++++++++++++++++++++
"""
def getFlags(ito): #pass intense object
    
    # Ensure non-nan lat/lon before neighbour checks (issue for some Sicily stations)
    if np.isfinite(ito.latitude) and np.isfinite(ito.longitude):
        #print("hourly neighbours")
        ito.QC_hourly_neighbours, ito.QC_hourly_neighbours_dry = checkHourlyNeighbours(ito)
        #print("daily neighbours")
        ito.QC_daily_neighbours, ito.QC_offset, ito.QC_preQC_affinity_index, ito.QC_preQC_pearson_coefficient, ito.QC_factor_daily, ito.QC_daily_neighbours_dry = checkDailyNeighbours(ito)
        #print("monthly neighbours")
        ito.QC_monthly_neighbours, ito.QC_factor_monthly = checkMonthlyNeighbours(ito)
    #print("world record")
    ito.QC_world_record = worldRecordCheckTs(ito)
    #print("rx1day")
    ito.QC_Rx1day = Rx1dayCheckTs(ito)
    #print("cwd")
    ##ito.QC_CWD = CWDCheck(ito)
    #print("cdd")
    ito.QC_CDD = CDDCheck(ito)
    #print("daily accumulations")
    ito.QC_daily_accumualtions = dailyAccumsCheck(ito)
    #print("monthly accumulations")
    ito.QC_monthly_accumulations = monthlyAccumsCheck(ito)
    #print("streaks")
    ito.QC_streaks = streaksCheck(ito)
    #print("percentiles")
    ito.QC_percentiles = checkPercentiles(ito)
    #print("k-largest")
    ito.QC_k_largest = checkKLargest(ito)
    #print("days of week")
    ito.QC_days_of_week = checkDaysOfWeek(ito)
    #print("hours of day")
    ito.QC_hours_of_day = checkHoursOfDay(ito)
    #print("intermittency")
    ito.QC_intermittency = checkIntermittency(ito)
    #print("breakpoint")
    ito.QC_breakpoint = checkBreakPoint(ito)
    #print("r99ptot")
    ito.QC_R99pTOT = R99pTOTCheckAnnual(ito)
    #print("prcptot")
    ito.QC_PRCPTOT = PRCPTOTCheckAnnual(ito)
    #print("change min value")
    ito.QC_change_min_value = changeInMinValCheck(ito)
    
    return ito

#++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# Process a folder (country) - q argument used in multiprocessing

def processFolder(folderToCheck, q=None):
    print(folderToCheck)
    if not os.path.exists(qcFolder + "/" + folderToCheck[:-4]):
        os.makedirs(qcFolder + "/" + folderToCheck[:-4])
    #errorPath = "/media/nas/x21971/QualityControlledData_v7/error_" + folderToCheck[:-4] + ".txt"
    errorPath = qcFolder + '/' + folderToCheck[:-4] + "/error_" + folderToCheck[:-4] + ".txt"
    if not os.path.exists(errorPath):
        errorFile = open(errorPath, "w")
    else:
        errorFile = open(errorPath, "a")
    existingFiles = os.listdir(qcFolder + "/" + folderToCheck[:-4])
    zf = zipfile.ZipFile(origFolder + "/" + folderToCheck, "r")
    filesList = zf.namelist()
    for file in filesList:
        if file == "France/":
            pass
        elif file[:-4] + "_QC.txt" in existingFiles:
            print("already done")
        else:
            try:
                d = zf.open(file, mode="r")
                qc = ex.readIntense(d, only_metadata=False, opened=True)
                qc = getFlags(qc)
                #ex.Series.write_QC(qc, qcFolder + "/" + folderToCheck[:-4])
            except:
                errorFile.write(file + "\n")
    errorFile.close()
    zf.close()

def findFilesToProcess(foldersToCheck):
    filesToProcess = []
    fileFolders = []
    for folderToCheck in foldersToCheck:
        
        # Check for existence of output folder - make if need be
        if not os.path.exists(qcFolder + "/" + folderToCheck[:-4] + "/Flags"):
            os.makedirs(qcFolder + "/" + folderToCheck[:-4] + "/Flags")
        existingFiles = os.listdir(qcFolder + "/" + folderToCheck[:-4] + "/Flags")
        
        # dp 13/12/2019 (01o1) - temporarily altered existingFiles for assessing normalised differences in 01o1 (i.e. different output folder)
        # - uncomment line below and remove the line after to get back to normal use
        #existingFiles = os.listdir(qcFolder + "/" + folderToCheck[:-4] + '/' + 
        #    qc_version + '/Flags/')
        ##existingFiles = []
        
        # Get list of raw (formatted) files to process
        zf = zipfile.ZipFile(origFolder + "/" + folderToCheck, "r")
        filesList = zf.namelist()
        for file in filesList:
            #if file == "France/":
            #    pass
            #elif file[:-4] + "_QC.txt" in existingFiles:
            if file[:-4] + "_QC.txt" in existingFiles:
                pass
            else:
                filesToProcess.append(file)
                fileFolders.append(folderToCheck)
        zf.close()
    return filesToProcess, fileFolders

def processFile(file, q=None): 
    
    # work out file index with a counter and pass as argument
    #print(file)
    folderToCheck = fileFolders[filesToProcess.index(file)]
    zf = zipfile.ZipFile(origFolder + "/" + folderToCheck, "r")
    ##try:
    d = zf.open(file, mode="r")
    
    #qc = ex.readIntense(d, only_metadata=False, opened=True)
    #print(qc.data)
    #sys.exit()
    
    try:
        qc = ex.readIntense(d, only_metadata=False, opened=True)
        print(file)
        successful_read = True
    except:
        print(file, "- read failed")
        successful_read = False
    
    if successful_read:
        qc = getFlags(qc)
        # ex.Series.write_QC(qc, qcFolder + "/" + folderToCheck[:-4]) # *** old - use line below to put in flags folder ***
        
        # for testing
        ## ex.Series.write_QC(qc, qcFolder + "/" + folderToCheck[:-4] + '/' + 
        ##    qc_version + '/Flags/')
        
        # for global run
        ex.Series.write_QC(qc, qcFolder + "/" + folderToCheck[:-4] + "/Flags")
    
    ##except:
    #errorFile.write(file + "\n")
    ##pass
    d.close()
    zf.close()

#++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

# prepare ETCCDI variables
# R99pTOTData, R99pTOTFilled = prepETCCDIvariable('/media/nas/x21971/ETCCDI/GHCND_R99p_1951-2018_RegularGrid_global_2.5x2.5deg_LSmask.nc', 'ETCCDI Max R99pTOT (mm)', 'max', 'Ann')
# PRCPTOTData, PRCPTOTFilled = prepETCCDIvariable('/media/nas/x21971/ETCCDI/GHCND_PRCPTOT_1951-2018_RegularGrid_global_2.5x2.5deg_LSmask.nc', 'ETCCDI Max PRCPTOT (mm)', 'max')
# CWDData, CWDFilled = prepETCCDIvariable('/media/nas/x21971/ETCCDI/GHCND_CWD_1951-2018_RegularGrid_global_2.5x2.5deg_LSmask.nc', 'ETCCDI Max CWD (days)', 'max')
# CDDData, CDDFilled = prepETCCDIvariable('/media/nas/x21971/ETCCDI/GHCND_CDD_1951-2018_RegularGrid_global_2.5x2.5deg_LSmask.nc', 'ETCCDI Max CDD (days)', 'max')
# SDIIData, SDIIFilled = prepETCCDIvariable('/media/nas/x21971/ETCCDI/GHCND_SDII_1951-2018_RegularGrid_global_2.5x2.5deg_LSmask.nc', 'ETCCDI Max SDII (mm)', 'mean')
# Rx1dayData, Rx1dayFilled = prepETCCDIvariable('/media/nas/x21971/ETCCDI/GHCND_Rx1day_1951-2018_RegularGrid_global_2.5x2.5deg_LSmask.nc', 'ETCCDI Max Rx1day (mm)', 'max')
etccdi_data = {"GHCNDEX": {}, "HADEX2": {}}
etccdi_data_folder = '/media/nas/x21971/ETCCDI_02/'
etccdi_indices = ['CWD', 'CDD', 'R99p', 'PRCPTOT', 'SDII', 'Rx1day']
periods = {"GHCNDEX": '1951-2018', "HADEX2": '1951-2010'}
aggregations = {}
for index in etccdi_indices:
    aggregations[index] = 'max'
aggregations['SDII'] = 'mean'
for data_source in etccdi_data.keys():
    for index in etccdi_indices:
        etccdi_data_path = (etccdi_data_folder + 'RawData_' + data_source + 
            '_' + index + '_' + periods[data_source] + 
            '_ANN_from-90to90_from-180to180.nc')
        etccdi_data[data_source][index] = prepETCCDIvariable(etccdi_data_path, 
            index, aggregations[index], data_source)

# Test retrieval of ETCCDI values
# index_name = 'CDD'
# #lon = 11.26
# #lat = 51.24
# #lon = 9.0
# #lat = 42.0
# #lon = 355 # -5.0
# #lat = 25 # 35.0
# lon = 11.25001
# lat = 51.25
# val, filled = get_ETCCDI_value(index_name, lon, lat)
# print(val, filled)
# sys.exit()

# create kd tree of monthly gauges ++++++++++++++++++++++++++++++++++++++

##THIS_FOLDER = os.path.dirname(os.path.abspath(__file__))
THIS_FOLDER = '/media/nas/x21971/PythonLessons/Python_3'
my_file = os.path.join(THIS_FOLDER, 'statlex_monthly.dat')
##print(os.path.isfile(my_file)) 
monthlyInfo = open(my_file, "r")

monthlyNames = []
monthlyDates = []
monthlyCoords = []

monthlyInfo = open(my_file, "r")#"/fish://extelewi@oflxs809/media/nas/x21971/statlex_monthly.dat"
monthlyInfo.readline()

for line in monthlyInfo:
  lineList=[line[0:10], line[11:54], line[54:62], line[62:73], line[73:79], line[125:135], line[137:147]]
  sid, name,lat, lon, elv, sd, ed =[l.strip() for l in lineList]
  try:
    sd = datetime.datetime.strptime(sd, "%d.%m.%Y")
    ed = datetime.datetime.strptime(ed, "%d.%m.%Y")
  except:
    sd = None
    ed = None
    
  if elv == "-999":
    elv = 100 #use 100m above seal level as a default elevation
  else:
    elv = float(elv)
    
  if sd==None or ed==None:
    pass
  else:
    monthlyNames.append(sid)
    monthlyDates.append((sd, ed))
    monthlyCoords.append((float(lat), float(lon), elv))
    
converted_monthlyCoords = [geodetic_to_ecef(a,b,c) for a,b,c in monthlyCoords]
monthlyTree = sp.KDTree(converted_monthlyCoords)

#+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

# create kd tree of daily gauges ++++++++++++++++++++++++++++++++++++++

##THIS_FOLDER = os.path.dirname(os.path.abspath(__file__))
THIS_FOLDER = '/media/nas/x21971/PythonLessons/Python_3'
my_file = os.path.join(THIS_FOLDER, 'statlex_daily')
##print(os.path.isfile(my_file)) 
dailyInfo = open(my_file, "r")

dailyNames = []
dailyDates = []
dailyCoords = []

dailyInfo.readline()

for line in dailyInfo:
  lineList=[line[0:10], line[11:54], line[54:62], line[62:73], line[73:79], line[125:135], line[137:147]]
  sid, name,lat, lon, elv, sd, ed =[l.strip() for l in lineList]
  try:
    sd = datetime.datetime.strptime(sd, "%d.%m.%Y")
    ed = datetime.datetime.strptime(ed, "%d.%m.%Y")
  except:
    sd = None
    ed = None
    
  if elv == "-999":
    elv = 100 #use 100m above sea level as a default elevation
  else:
    elv = float(elv)
    
  if sd==None or ed==None:
    pass
  else:
    dailyNames.append(sid)
    dailyDates.append((sd, ed))
    dailyCoords.append((float(lat), float(lon), elv))
    
converted_dailyCoords = [geodetic_to_ecef(a,b,c) for a,b,c in dailyCoords]
tree = sp.KDTree(converted_dailyCoords)

#+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++


# create kd tree of hourly gauges ++++++++++++++++++++++++++++++++++++++

##THIS_FOLDER = os.path.dirname(os.path.abspath(__file__))
THIS_FOLDER = '/media/nas/x21971/PythonLessons/Python_3'
#my_file = os.path.join(THIS_FOLDER, 'statlex_hourly.dat')
my_file = os.path.join(THIS_FOLDER, 'statlex_hourly_200108.dat')
##print(os.path.isfile(my_file)) 
hourlynInfo = open(my_file, "r")

hourlynNames = []
hourlynDates = []
hourlynCoords = []
hourlynPaths = []
converted_hourlynCoords = []

hourlynNamesT = []
hourlynDatesT = []
hourlynCoordsT = []
hourlynPathsT = []

hourlynInfo.readline()

for line in hourlynInfo:
  sid, lat, lon, sd, ed, elv, hpath=line.rstrip().split(",")

  try:
    sd = datetime.datetime.strptime(sd, "%Y%m%d%H")
    ed = datetime.datetime.strptime(ed, "%Y%m%d%H")
  except:
    sd = None
    ed = None

  if elv.lower() == "na" or elv == "m" or elv == "nam" or elv ==  "nan":
    elv = 100 #use 100m above sea level as a default elevation
  else:
    if elv.endswith("m"):
      elv = elv[:-1]
    try:
      elv = float(elv)
    except:
      elv = 100
    
  if sd==None or ed==None:
    pass
  else:
    
    # Only append if >=3 years of record (no point having potential neighbours 
    # without substantial data)
    # - Also ensure that no duplicates arising from e.g. duplicates in Australia1min.zip
    date_diff = ed - sd
    if date_diff.days >= 3*365:
      if sid not in hourlynNamesT:
        hourlynNamesT.append(sid)
        hourlynDatesT.append((sd, ed))
        hourlynCoordsT.append((float(lat), float(lon), elv))
        hourlynPathsT.append(hpath)
    
converted_hourlynCoordsT = [geodetic_to_ecef(a,b,c) for a,b,c in hourlynCoordsT]

for i in range(len(converted_hourlynCoordsT)):
    addIt = 1
    for j in converted_hourlynCoordsT[i]:
        if np.isnan(j):
            addIt = 0

    if addIt == 1:
        hourlynNames.append(hourlynNamesT[i])
        hourlynDates.append(hourlynDatesT[i])
        hourlynCoords.append(hourlynCoordsT[i])
        hourlynPaths.append(hourlynPathsT[i])
        converted_hourlynCoords.append(converted_hourlynCoordsT[i])

hourlynTree = sp.KDTree(converted_hourlynCoords)

#+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

# Selected upper bound for hourly world record - see:
#http://www.nws.noaa.gov/oh/hdsc/record_precip/record_precip_world.html
#http://www.bom.gov.au/water/designRainfalls/rainfallEvents/worldRecRainfall.shtml
#https://wmo.asu.edu/content/world-meteorological-organization-global-weather-climate-extremes-archive
world_records = {'hourly': 401.0, 'daily': 1825.0} # mm

origFolder = "/media/nas/x21971/QualityControlledData"
#qcFolder = "/media/nas/x21971/QualityControlledData_v7"
qcFolder = "/media/nas/x21971/QC_10"
##errorFile = open("/media/nas/x21971/QualityControlledData_v7/error.txt", "w")
numProcesses = 4

# Paths for testing
#qcFolder = "/media/nas/x21971/DP/QC_Checks"
#qc_version = '01qz'

foldersToCheck = []
for file in os.listdir(origFolder):
    if file.endswith(".zip"):
        foldersToCheck.append(file)
##foldersToCheck = ["Germany.zip", "Japan.zip", "Australia5min"]
#foldersToCheck = ['NewZealand.zip'] # ['Germany.zip']

# Multiprocessing by folder (country)
# if __name__ == '__main__':
    # pool = Pool(processes=numProcesses)
    # m = Manager()
    # q = m.Queue()
    # for folderToCheck in foldersToCheck:
        # pool.apply_async(processFolder, [folderToCheck, q])
    # pool.close()
    # pool.join()

    # results = []
    # while not q.empty():
        # try:
            # results.append(q.get())
        # except:
            # pass

# USE THE MULTIPROCESSING BELOW, NOT THE ONE ABOVE!
#'''
# Multiprocessing by file (gauge)
# - first get lists of files to process and corresponding folder (country)
filesToProcess, fileFolders = findFilesToProcess(foldersToCheck)
if __name__ == '__main__':
    pool = Pool(processes=numProcesses)
    m = Manager()
    q = m.Queue()
    for file in filesToProcess:
        pool.apply_async(processFile, [file, q])
    pool.close()
    pool.join()

    results = []
    while not q.empty():
        try:
            results.append(q.get())
        except:
            pass
#'''

#'''
# Additional sweep(s) with serial processing
time.sleep(60)
filesToProcess, fileFolders = findFilesToProcess(foldersToCheck)
for file in filesToProcess:
    processFile(file)
#'''

#test = ex.readIntense("/media/nas/x21971/temp/DE_13662.txt", only_metadata=False, opened=False)
#test = getFlags(test)
#ex.Series.write_QC(test, "/media/nas/x21971/temp")

# -----------------------------------------------------------------------------
# For checks

# Paths
#qcFolder = "/media/nas/x21971/DP/QC_Checks"
#qc_version = '01l'

# File to save number of valid neighbours
#nbrs_path = "/media/nas/x21971/DP/QC_Checks/Malaysia/Malaysia_NumNeighbours_01.csv"
#fh_nbrs = open(nbrs_path, "w")
#fh_nbrs.write("Station,Interval,NumNeighbours\n")

# Folder to save normalised differences
#nd_folder = '/media/nas/x21971/DP/QC_Checks/Germany/01o_ND/'

'''
# ---
# File to save distribution of normalised differences in neighbour checks
dist_path = "/media/nas/x21971/DP/QC_Checks/Germany/Germany_NeighbourDiffs_01l_02.csv"
fh_dist = open(dist_path, "w")
fh_dist.write("Station,Check,Neighbour,Bin,Frequency\n")

# File to save key percentiles etc of normalised differences in neighbour checks
thrsh_path = "/media/nas/x21971/DP/QC_Checks/Germany/Germany_NeighbourThresholds_01l_02.csv"
fh_thrsh = open(thrsh_path, "w")
fh_thrsh.write("Station,Check,Neighbour,Statistic,Value\n")
# ---
'''

# Process a file(s)
##filesToProcess = ['MY_negeri sembilan_2921013.txt'] # ['MY_kuala lumpur_3016103.txt'] # ['MY_pahang_2834001.txt'] # ['MY_johor_1541139.txt'] # ['MY_pahang_4419047.txt'] # ['MY_johor_1437116.txt'] # ['MY_kuala lumpur_3116003.txt'] # ['MY_terengganu_5328043.txt'] # ['DE_03098.txt'] # ['MY_kuala lumpur_3016102.txt']
##fileFolders = ['Malaysia.zip'] # ['Germany.zip'] # 
##filesToProcess = ['DE_01605.txt'] # ['DE_02483.txt'] # ['DE_00599.txt'] # ['DE_00596.txt'] # ['DE_02483.txt'] # ['DE_15207.txt'] # 
##fileFolders = ['Germany.zip']
##processFile(filesToProcess[0])
#filesToProcess = [
#    'DE_00596.txt', 'DE_00691.txt', 'DE_00991.txt', 'DE_01050.txt', 'DE_01051.txt',
#    'DE_01346.txt', 'DE_01769.txt', 'DE_01832.txt', 'DE_01848.txt', 'DE_02174.txt',
#    'DE_02175.txt', 'DE_02272.txt', 'DE_02578.txt', 'DE_02749.txt', 'DE_02890.txt',
#    'DE_03279.txt', 'DE_03740.txt', 'DE_04600.txt', 'DE_07323.txt', 'DE_07333.txt',
#    'DE_07343.txt', 'DE_13654.txt', 'DE_13688.txt']
#fileFolders = ['Germany.zip' for i in range(len(filesToProcess))]
#filesToProcess = ['IT_684.txt'] # ['NZ_40980.txt'] # ['AU_022843.txt']
#fileFolders = ['Italy.zip'] # ['NewZealand.zip'] # ['Australia1min.zip']
#for file in filesToProcess:
#    processFile(file)
#sys.exit()

# Process a country
# output_path = qcFolder + fileFolders[filesToProcess.index(file)][:-4] + "/" + file.replace(".txt", "_QC.txt") 
#foldersToCheck = ['Malaysia.zip']
#foldersToCheck = ['NewZealand.zip'] # ['Germany.zip'] # 
#filesToProcess, fileFolders = findFilesToProcess(foldersToCheck)
#print(len(filesToProcess))
#input()
#for file in filesToProcess:
#    processFile(file)
'''
# --
import numpy.random
inds = np.random.choice(len(filesToProcess), 100)
for file in filesToProcess:
    i = filesToProcess.index(file)
    #output_path = qcFolder + fileFolders[filesToProcess.index(file)][:-4] + "/" + file.replace(".txt", "_QC.txt") 
    if i in inds:
        processFile(file)
# --
'''

#fh_nbrs.close()

#fh_dist.close()
#fh_thrsh.close()

#errorFile.close()
#print("Finished")
