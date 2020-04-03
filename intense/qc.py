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

import os
from datetime import datetime, timedelta, date

import pandas as pd
import numpy as np
import rpy2.robjects as robjects
from rpy2.robjects.packages import importr
import scipy.interpolate
import scipy.stats
from rpy2.robjects.vectors import StrVector
from rpy2.rinterface import RRuntimeError

from intense.intense import try_float, try_list, try_int
from .intense import Series
from intense import utils


def install_r_package(package_name):
    utils = importr('utils')
    utils.install_packages(StrVector([package_name]), repos ='http://cran.us.r-project.org')


try:
    trend = importr('trend')
except RRuntimeError:
    # Had to run line below on macOS:
    # sudo installer -pkg /Library/Developer/CommandLineTools/Packages/macOS_SDK_headers_for_macOS_10.14.pkg -target /
    install_r_package('trend')
    trend = importr('trend')

"""
+++++++++++++++++++++++++++++++++++++++++++ Basic checks +++++++++++++++++++++++++++++++++++++++++++
"""


class Qc:

    def __init__(self,
                 series: Series,
                 etccdi_data_folder=None,
                 hourly_n_names=None,
                 hourly_n_dates=None,
                 hourly_n_coords=None,
                 hourly_n_paths=None,
                 hourly_n_tree=None,
                 use_daily_neighbours=False,
                 daily_names=None,
                 daily_dates=None,
                 daily_coords=None,
                 tree=None,
                 use_monthly_neighbours=False,
                 monthly_names=None,
                 monthly_dates=None,
                 monthly_coords=None,
                 monthly_tree=None,

                 hourly_neighbours=None,
                 hourly_neighbours_dry=None,
                 daily_neighbours=None,
                 daily_neighbours_dry=None,
                 monthly_neighbours=None,
                 world_record=None,
                 Rx1day=None,
                 CWD=None,
                 CDD=None,
                 daily_accumualtions=None,
                 monthly_accumulations=None,
                 streaks=None,
                 percentiles=["NA", "NA"],
                 k_largest=["NA", "NA", "NA"],
                 days_of_week="NA",
                 hours_of_day="NA",
                 intermittency="NA",
                 breakpoint="NA",
                 R99pTOT="NA",
                 PRCPTOT="NA",
                 change_min_value="NA",
                 offset="NA",
                 preQC_affinity_index="NA",
                 preQC_pearson_coefficient="NA",
                 factor_daily="NA",
                 factor_monthly=None
                 
                 ):
        self.series = series

        self.etcdii_data = etccdi_data_folder
        if etccdi_data_folder is not None:
            self.etcdii_data = utils.read_etccdi_data(etccdi_data_folder)

        self.hourly_n_names = hourly_n_names
        self.hourly_n_dates = hourly_n_dates
        self.hourly_n_coords = hourly_n_coords
        self.hourly_n_paths = hourly_n_paths
        self.hourly_n_tree = hourly_n_tree
        self.use_daily_neighbours = use_daily_neighbours
        self.daily_names = daily_names
        self.daily_dates = daily_dates
        self.daily_coords = daily_coords
        self.tree = tree
        self.use_monthly_neighbours = use_monthly_neighbours
        self.monthly_names = monthly_names
        self.monthly_dates = monthly_dates
        self.monthly_coords = monthly_coords
        self.monthly_tree = monthly_tree

        self.hourly_neighbours = hourly_neighbours
        self.hourly_neighbours_dry = hourly_neighbours_dry
        self.daily_neighbours = daily_neighbours
        self.daily_neighbours_dry = daily_neighbours_dry
        self.monthly_neighbours = monthly_neighbours
        self.world_record = world_record
        self.Rx1day = Rx1day
        self.CWD = CWD
        self.CDD = CDD
        self.daily_accumualtions = daily_accumualtions
        self.monthly_accumulations = monthly_accumulations
        self.streaks = streaks
        self.percentiles = percentiles
        self.k_largest = k_largest
        self.days_of_week = days_of_week
        self.hours_of_day = hours_of_day
        self.intermittency = intermittency
        self.breakpoint = breakpoint
        self.R99pTOT = R99pTOT
        self.PRCPTOT = PRCPTOT
        self.change_min_value = change_min_value
        self.offset = offset
        self.preQC_affinity_index = preQC_affinity_index
        self.preQC_pearson_coefficient = preQC_pearson_coefficient
        self.factor_daily = factor_daily
        self.factor_monthly = factor_monthly

    # Indicative check to flag years with 95th or 99th percentiles equal to zero.
    def check_percentiles(self):
        perc95 = self.series.data.groupby(pd.Grouper(freq='A')).quantile(.95)
        perc99 = self.series.data.groupby(pd.Grouper(freq='A')).quantile(.99)

        return [[d.year for d in list(perc95[perc95 == 0].index)], [d.year for d in list(perc99[perc99 == 0].index)]]


    # Indicative check to flag years with K-largest values equal to zero.
    def check_k_largest(self):
        k1 = self.series.data.groupby(pd.Grouper(freq='A')).nlargest(n=1).min(level=0)
        k5 = self.series.data.groupby(pd.Grouper(freq='A')).nlargest(n=5).min(level=0)
        k10 = self.series.data.groupby(pd.Grouper(freq='A')).nlargest(n=10).min(level=0)

        return [[d.year for d in list(k1[k1 == 0].index)], [d.year for d in list(k5[k5 == 0].index)],
                [d.year for d in list(k10[k10 == 0].index)]]


    # Indicative, checks if proportions of rainfall in each day is significantly different
    def check_days_of_week(self):
        # 0 is monday, 1 is tuesday etc...
        days = self.series.data.groupby(lambda x: x.weekday).mean()
        popmean = self.series.data.mean()
        p = scipy.stats.ttest_1samp(days, popmean)[1]
        if p < 0.01:  # different
            return 1
        else:
            return 0


    # Indicative, hourly analogue to daily check
    def check_hours_of_day(self):
        # 0 is midnight, 1 is 01:00 etc...
        hours = self.series.data.groupby(lambda x: x.hour).mean()
        popmean = self.series.data.mean()
        p = scipy.stats.ttest_1samp(hours, popmean)[1]
        if p < 0.01:  # different
            return 1
        else:
            return 0


    # Annual check for discontinuous records.
    # Returns years where more than 5 no data periods are bounded by zeros.
    # A no data period is defined as 2 or more consecutive missing values.
    # Return years where more than 5 no data periods are bounded by zeros
    # A no data period is defined as 2 or more consecutive missing values
    # For a year to be flagged no data periods must occur in at least 5 different days

    def check_intermittency(self):
        # Shift data +/- 1 hour to help identify missing data periods with vectorised approach
        df = self.series.data.copy().to_frame()
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

        # Select only the periods preceded and followed by dry hours
        start_inds2 = []
        end_inds2 = []
        if len(start_inds) > 0:
            for si, ei in zip(start_inds, end_inds):

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
    def check_break_point(self):
        x = self.series.data.resample("D").sum().values
        x = x[~np.isnan(x)]
        x = x
        x = robjects.FloatVector(x)

        # using the pettitt test
        pettitt = robjects.r['pettitt.test']
        p = pettitt(x).rx('p.value')[0][0]  # gives the p-value if p-value is below 0.05 (or 0.01) there might be a change point
        if p < 0.01:  # different
            return 1
        else:
            return 0

    """
    ++++++++++++++++++++++++++++++++++ Threshold Checks +++++++++++++++++++++++++++++++++++
    """


    def world_record_check_ts(self):
        wrcts = self.series.data.map(lambda x: utils.world_record_check(x))
        return list(wrcts)

    # Checks against ETCCDI indices ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

    # We are using [ETCCDI indicies](http://etccdi.pacificclimate.org/list_27_indices.shtml)
    # to act as thresholds for expected hourly values.
    # In particular, we are using index 17:
    # Rx1day, Monthly maximum 1-day precipitation:
    # Here I'm just going to start by using the maximum of the annual maximums,
    # to give us the biggest possible daily value for each available square.
    # First we must read in the indicies from the netCDF file:
    # We then calculate the maximum rainfall value over the whole period for each gridsquare.


    # ++++++++ Rx1day: check hourly values against maximum 1-day precipitation   ++++++++


    def rx1day_check_ts(self):
        p_max, p_max_filled = utils.get_etccdi_value(self.etcdii_data, 'Rx1day', self.series.longitude, self.series.latitude)
        df = self.series.data.to_frame("GSDR")

        ''' If you have a high density of daily gauges, you can calculate Rx1day stats from that and compare them to a daily
            total from the hourly gauges. The ETCCDI gauge density is not high enough to do this so we use it as a threshold
            check for hourly values
        
        df["roll"] = np.around(df.GSDR.rolling(window=24, center=False, min_periods=24).sum())
        df["r1dcts"] = df.roll.map(lambda x: dayCheck(x, pMax, pMaxFilled))
        '''
        if np.isfinite(p_max) or np.isfinite(p_max_filled):
            df["r1dcts"] = df.GSDR.map(lambda x: utils.day_check(x, p_max, p_max_filled))
        else:
            df["r1dcts"] = np.nan

        return list(df.r1dcts)


    # ++++++++ Other precipitation index checks ++++++++

    # Indicative check against R99pTOT: R99pTOT. Annual total PRCP when RR > 99p:

    def r99ptot_check_annual(self):
        p_max, p_max_filled = utils.get_etccdi_value(self.etcdii_data, 'R99p', self.series.longitude, self.series.latitude)

        if np.isfinite(p_max) or np.isfinite(p_max_filled):

            daily_ts = self.series.data.resample(
                "D").sum()  # this changes depending on which version of pandas youre using. o.14 requires how agument,
            # later requires .sum

            perc99 = daily_ts.groupby(pd.Grouper(freq='A')).quantile(.99)
            py = list(perc99.index.year)
            pv = list(perc99)
            p_dict = {}
            for p in range(len(py)):
                p_dict[py[p]] = pv[p]
            daily_df = daily_ts.to_frame("daily")
            daily_df["year"] = daily_df.index.year
            daily_df["p99"] = daily_df.apply(lambda row: p_dict[row.year], axis=1)
            daily_df["filtered"] = daily_df.daily.where(daily_df.daily >= daily_df.p99)
            perc99_tot = daily_df.groupby(pd.Grouper(freq='A')).sum()
            tots = list(perc99_tot.filtered)
            checks = [utils.day_check(t, p_max, p_max_filled) for t in tots]

        else:
            checks = [np.nan]

        return checks


    # Indicative check against annual total: PRCPTOT. Annual total precipitation in wet days:

    def prcptot_check_annual(self):
        # pMax, pMaxFilled = getPRCPTOT(self.series.latitude, self.series.longitude)
        p_max, p_max_filled = utils.get_etccdi_value(self.etcdii_data, 'PRCPTOT', self.series.longitude, self.series.latitude)

        if np.isfinite(p_max) or np.isfinite(p_max_filled):
            ann_tots = self.series.data.groupby(pd.Grouper(freq='A')).sum()
            tots = list(ann_tots)
            checks = [utils.day_check(t, p_max, p_max_filled) for t in tots]
        else:
            checks = [np.nan]

        return checks


    # ++++++++ Long wet/dry spell checks ++++++++

    # ETCCDI provide an index for maximum length of wet spell.
    # We can use this to see if there are a suspicious number of consecutive wet hours recorded.
    # Consecutive Wet Days: Maximum length of wet spell, maximum number of consecutive days with RR = 1mm:
    # Let RRij be the daily precipitation amount on day i in period j.
    # Count the largest number of consecutive days where: RRij = 1mm


    def cwd_check(self):
        vals = self.series.data
        longest_wet_period, longest_wet_period_filled = utils.get_etccdi_value(self.etcdii_data, 'CWD', self.series.longitude, self.series.latitude)
        start_index_list, duration_list = utils.get_wet_periods(vals)
        flags_list = [0 for i in range(len(vals))]

        if np.isfinite(longest_wet_period) or np.isfinite(longest_wet_period_filled):

            for wetPeriod in range(len(start_index_list)):
                flag = utils.spell_check(duration_list[wetPeriod], longest_wet_period, longest_wet_period_filled)

                for j in range(start_index_list[wetPeriod],
                               min(start_index_list[wetPeriod] + duration_list[wetPeriod], (len(flags_list) - 1)), 1):
                    flags_list[j] = flag

        else:
            flags_list = [np.nan for i in range(len(vals))]

        return flags_list


    # ### Long dry spells

    # ETCCDI provide an index for maximum length of dry spell.
    # We can use this to see if there are a suspicious number of consecutive dry hours recorded.
    # Consecutive Dry Days: Maximum length of dry spell, maximum number of consecutive days with RR < 1mm:
    # Let RRij be the daily precipitation amount on day i in period j.
    # Count the largest number of consecutive days where: RRij < 1mm


    def cdd_check(self):
        vals = list(self.series.data)
        longest_dry_period, longest_dry_period_filled = utils.get_etccdi_value(self.etcdii_data, 'CDD', self.series.longitude, self.series.latitude)

        start_index_list, duration_list = utils.get_dry_periods(vals)
        flags_list = [0 for i in range(len(vals))]

        if np.isfinite(longest_dry_period) or np.isfinite(longest_dry_period_filled):

            for dryPeriod in range(len(start_index_list)):
                flag = utils.spell_check(duration_list[dryPeriod], longest_dry_period, longest_dry_period_filled)

                for j in range(start_index_list[dryPeriod], start_index_list[dryPeriod] + duration_list[dryPeriod], 1):
                    flags_list[j] = flag
        else:
            flags_list = [np.nan for i in range(len(vals))]

        return flags_list


    # ++++++++++++++++++++++++++++++++++ Non-Threshold Checks +++++++++++++++++++++++++++++++++++


    def get_sdii(self):
        # *** CHECK HOURLY WORLD RECORD PRECIPITATION ***
        # ?? insert a check for whether gauge SDII exceeds minimum tip / resolution/precision ??

        # Remove any hours exceeding world record in the gauge record
        df1 = self.series.data.copy().to_frame()
        df1.columns = ['val']
        df1['val'] = np.where(df1['val'] > utils.world_records['hourly'], np.nan, df1['val'])

        # Aggregate gauge to daily and remove any days exceeding world record
        # - remove first and last days assuming might be incomplete
        df2 = df1.resample("D", label='left', closed='right').apply(lambda x: x.values.sum())
        df2 = df2.loc[(df2.index > df2.index.min()) & (df2.index < df2.index.max())]
        df2['val'] = np.where(df2['val'] > utils.world_records['daily'], np.nan, df2['val'])

        # Calculate SDII from gauge
        prcp_sum = df2.loc[df2['val'] >= 1.0, 'val'].sum()
        wetday_count = df2.loc[df2['val'] >= 1.0, 'val'].count()
        sdii_gauge = prcp_sum / float(wetday_count)

        # Retrieve SDII from gridded ETCCDI datasets
        sdii_cell, sdii_filled = utils.get_etccdi_value(self.etcdii_data, 'SDII', self.series.longitude, self.series.latitude)
        if np.isfinite(sdii_cell):
            sdii_gridded = sdii_cell
        else:
            if np.isfinite(sdii_filled):
                sdii_gridded = sdii_filled
            else:
                sdii_gridded = np.nan

        return [sdii_gridded, sdii_gauge]


    # ++++++++ Daily accumulation checks ++++++++


    def daily_accums_check(self):
        vals = list(self.series.data)

        mean_wet_day_val, mean_wet_day_val_filled = self.get_sdii()

        flags = [0 for i in range(len(vals))]

        for i in range(len(vals) - 24):
            day_val_list = vals[i:i + 24]
            flag = utils.daily_accums_day_check(day_val_list, mean_wet_day_val, mean_wet_day_val_filled)
            if flag > max(flags[i:i + 24]):
                flags[i:i + 24] = [flag for j in range(24)]

        return flags


    """
    ++++++++ Monthly accumulation checks ++++++++
    """


    def monthly_accums_day_check(month_list, mean_wet_day_val, mean_wet_day_val_filled):
        """Suspect monthly accumulations.
        Identified where only one hourly value is reported over a period of a month
        and that value exceeds the mean wet hour amount for the corresponding month."""

        if month_list[719] > 0:
            dry_hours = 0
            for i in range(719):
                if month_list[i] <= 0:
                    dry_hours += 1
            if dry_hours == 719:
                if np.isnan(mean_wet_day_val):
                    if month_list[719] > mean_wet_day_val_filled * 2:
                        return 2
                    else:
                        return 0
                else:
                    if month_list[719] > mean_wet_day_val * 2:
                        return 1
                    else:
                        return 0
            else:
                return 0
        else:
            return 0


    # Flags month prior to high value

    def monthly_accums_check(self):
        # Find threshold for wet hour following dry month (2 * mean wet day rainfall)
        mean_wetday_val, mean_wetday_val_filled = self.get_sdii()
        if np.isnan(mean_wetday_val):
            threshold = mean_wetday_val_filled * 2.0
        else:
            threshold = mean_wetday_val * 2.0

        # Lag values forwards and backwards to help identify consecutive value streaks
        df = self.series.data.copy().to_frame()
        df.columns = ['val']
        df['prev'] = df.shift(1)['val']
        df['next'] = df.shift(-1)['val']

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
                dict(start=start_inds, end=end_inds))
            df1['diff'] = df1['end'] - df1['start'] + 1

            # Subset on periods with length of >= 720 days
            df1 = df1.loc[df1['diff'] >= 720]

            # Code below would adjust any periods >720 days to be =720 days (i.e.
            # alter period start date) - not currently used

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
                rainfall_sum = df.iloc[idx + 1:idx + 1 + 23, df.columns.get_loc('val')].sum()
                if rainfall_sum > 0.0:
                    df2.iloc[i, df2.columns.get_loc('end_nextday_wet')] = 1
                i += 1

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
                for i in range(int(row[1].start), int(row[1].end + 1)):
                    flags[i] = int(row[1].flag)

        # If no periods identified (e.g. lots of missing data) return nans
        else:
            flags = [np.nan for i in range(len(df['val']))]

        return flags


    # ++++++++ Streak checks ++++++++

    # Streaks: This is where you see the same value repeated in a run.
    # Currently this records streaks of 2hrs in a row or more over 2 x Monthly mean rainfall.
    # It is considered to be unlikely that you would see even 2 consecutive large rainfall amounts.
    # For this code I have substituted the monthly mean rainfall for SDII as I want the thresholds
    # to be independent of the rainfall time series as the global dataset is of highly variable quality.

    def streaks_check(self):
        # Find wet day rainfall threshold (for streaks of any length)
        # mean_wetday_val, mean_wetday_val_filled = getSDII(self.series.latitude, self.series.longitude)
        mean_wetday_val, mean_wetday_val_filled = self.get_sdii()
        threshold = mean_wetday_val * 2.0
        if np.isnan(mean_wetday_val):
            threshold = mean_wetday_val_filled * 2.0

        # Lag values forwards and backwards to help identify consecutive value streaks
        df = self.series.data.copy().to_frame()
        df.columns = ['val']
        df['prev'] = df.shift(1)['val']
        df['next'] = df.shift(-1)['val']
        df['prev'] = np.where(df['prev'].isnull(), 0, df['prev'])
        df['next'] = np.where(df['next'].isnull(), 0, df['next'])

        # Look for streaks of values exceeding 2 * mean wet day rainfall
        df1 = pd.DataFrame(
            dict(start=np.flatnonzero((df.val != df.prev) & (df.val == df.next) &
                                      (df.val >= threshold)),
                 end=np.flatnonzero((df.val == df.prev) & (df.val != df.next) &
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
                dict(start=np.flatnonzero((df.val != df.prev) & (df.val == df.next) &
                                          (df.val > df.year_min)),
                     end=np.flatnonzero((df.val == df.prev) & (df.val != df.next) &
                                        (df.val > df.year_min))))

        # If above fails then use one value for all years as threshold, based on
        # maximum of annual minima, ensuring >= 0.1 and <= 2.6 (done above) (FL10)
        except:
            min_threshold = np.max(df99['year_min'])
            df2 = pd.DataFrame(
                dict(start=np.flatnonzero((df.val != df.prev) & (df.val == df.next) &
                                          (df.val > min_threshold)),
                     end=np.flatnonzero((df.val == df.prev) & (df.val != df.next) &
                                        (df.val > min_threshold))))

        # Subset on periods of >= 12 consecutive values
        df2['diff'] = df2['end'] - df2['start'] + 1
        df2 = df2.loc[df2['diff'] >= 12]

        flag = 1
        if np.isnan(mean_wetday_val):
            flag = 2
        df1['flag'] = flag
        df2['flag'] = 3
        df3 = df1.append(df2)

        # Make list of flags
        flags = [0 for i in range(len(df['val']))]
        for row in df3.iterrows():
            for i in range(row[1].start, row[1].end + 1):
                flags[i] = row[1].flag

        return flags

    # ++++++++ Change in minimum value check ++++++++


    # Change in minimum value: This is an homogeneity check to see if the resolution of the data has changed.
    # Currently, I am including a flag if there is a year of no data as that seems pretty bad to me.

    # Alternative implementation to return list of years where the minimum value >0
    # differs from the data precision/resolution identified in the raw (pre-QC) files
    def change_in_min_val_check(self):
        # Filter on values >0
        df = self.series.data[self.series.data > 0.0].to_frame()
        df.columns = ['val']

        # Find minimum by year
        df = df.groupby(df.index.year).min()

        # List of years differing from inferred precision in raw (pre-QC) data files
        df = df.loc[df['val'] != self.series.resolution]
        flag_years = df.index.tolist()
        if len(flag_years) > 0:
            change_flag = 1
        else:
            change_flag = 0

        return [change_flag, flag_years]


    """
    ++++++++++++++++++++++++++ Neighbour Checks - Basic functions +++++++++++++++++++++++++++
    """


    # Helper function, finds hourly neighbour stations ++++++++++++++++++++++++++++
    def find_hourly_neighbours(self):
        # float("nan") returns np.nan so needs to be handled separately (occurs in some Italy (Sicily) files)
        # whereas float("NA") returns value error (i.e. convention in most raw/formatted files)
        try:
            if elv != "nan":
                elv = float(self.series.elevation)
            else:
                elv = 100
        except:
            elv = 100

        converted_hourly_coords = utils.geodetic_to_ecef(self.series.latitude, self.series.longitude, elv)
        dist, index = self.hourly_n_tree.query(converted_hourly_coords,
                                          k=30)
        # K needs to be equal or less than the number
        # of stations available in the database
        overlap = []
        paired_stations = []
        distance = []
        paths = []

        hourly_dates = (self.series.start_datetime, self.series.end_datetime)

        dist = [d for d in dist if np.isfinite(d)]
        index = [i for i,d in zip(index,dist) if np.isfinite(d)]

        counter = 0
        for i in range(len(dist)):
            dci = index[i]
            pol, ol = utils.calculate_overlap(hourly_dates, self.hourly_n_dates[dci])
            ps = self.hourly_n_names[dci]
            di = dist[i]
            pa = self.hourly_n_paths[dci]

            if di < 50000:  # must be within 50km
                if ol > 365 * 3:  # must have at least 3 years overlap
                    if counter < 11:  # want to select the closest 10, but the first one is always the target itself
                        overlap.append(ol)
                        paired_stations.append(ps)
                        distance.append(di)
                        paths.append(pa)
                        counter += 1

        if len(paired_stations) >= 3:
            return [paired_stations, paths]
        else:
            return [[], []]


    # Helper function, finds daily neighbour stations +++++++++++++++++++++++++++++
    def find_daily_neighbours(self):
        try:
            elv = float(self.series.elevation)
        except:
            elv = 100

        converted_hourly_coords = utils.geodetic_to_ecef(self.series.latitude, self.series.longitude, elv)

        dist, index = self.tree.query(converted_hourly_coords, k=30)

        overlap = []
        paired_stations = []
        distance = []

        hourly_dates = (self.series.start_datetime, self.series.end_datetime)

        counter = 0
        for i in range(len(dist)):
            dci = index[i]
            pol, ol = utils.calculate_overlap(hourly_dates, self.daily_dates[dci])
            ps = self.daily_names[dci]
            di = dist[i]

            if di < 50000:  # must be within 50km
                if ol > 365 * 3:  # must have at least 3 years overlap
                    if counter < 10:  # want to select the closest 10
                        overlap.append(ol)
                        paired_stations.append(ps)
                        distance.append(di)
                        counter += 1

        if len(paired_stations) >= 3:
            return paired_stations
        else:
            return []


    # Helper function, finds daily neighbour stations +++++++++++++++++++++++++++++
    def find_monthly_neighbours(self):
        try:
            elv = float(self.series.elevation)
        except:
            elv = 100

        converted_hourly_coords = utils.geodetic_to_ecef(self.series.latitude, self.series.longitude, elv)

        dist, index = self.monthly_tree.query(converted_hourly_coords, k=30)

        overlap = []
        paired_stations = []
        distance = []

        hourly_dates = (self.series.start_datetime, self.series.end_datetime)

        counter = 0
        for i in range(len(dist)):
            mci = index[i]
            pol, ol = utils.calculate_overlap(hourly_dates, self.monthly_dates[mci])
            ps = self.monthly_names[mci]
            di = dist[i]

            if di < 50000:  # must be within 50km
                if ol > 365 * 3:  # must have at least 3 years overlap
                    if counter < 10:  # want to select the closest 10
                        overlap.append(ol)
                        paired_stations.append(ps)
                        distance.append(di)
                        counter += 1

        if len(paired_stations) >= 3:
            return paired_stations
        else:
            return None


    # ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++


    # Match station id helper function ++++++++++++++++++++++++++++++++++++++++++++
    def find_identical_by_id(self, neighbour):  # this probably needs refining...
        match = 0
        if self.series.station_id[3:] in neighbour.name:
            match = 1
        if self.series.station_id[3:] in neighbour.station_id:
            match = 1
        if self.series.station_id[3:] in neighbour.wmo_id:
            match = 1
        if self.series.original_station_number in neighbour.name:
            match = 1
        if self.series.original_station_number in neighbour.station_id:
            match = 1
        if self.series.original_station_number in neighbour.wmo_id:
            match = 1
        if self.series.original_station_name in neighbour.name:
            match = 1
        if self.series.original_station_name in neighbour.station_id:
            match = 1
        if self.series.original_station_name in neighbour.wmo_id:
            match = 1

        return match


    """
    ++++++++++++++++++++++++++++++++++ GPCC functions -end +++++++++++++++++++++++++++++++++++
    """

    """
    ++++++++++++++++++++++++++++++++++ Neighbour Checks +++++++++++++++++++++++++++++++++++
    """


    # ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    def check_hourly_neighbours(self):
        df = self.series.data.to_frame("target")

        # convert hourly to daily 7am-7am
        df["roll"] = np.around(df.target.rolling(window=24, center=False, min_periods=24).sum(), 1)
        dfd = df[df.index.hour == 7]
        dts = list(dfd.index)
        daily_vals = list(dfd.roll)

        dts0 = []
        for dt in dts:
            s0 = dt - timedelta(days=1)
            dts0.append(date(s0.year, s0.month, s0.day))
        ts0 = pd.Series(daily_vals, index=dts0)

        neighbours, paths = self.find_hourly_neighbours()

        # dp 30/11/2019 - assuming neighbours[0] is the target
        if len(neighbours) > 1:

            # More GSDR bits here Liz: -+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-

            # get GSDR
            neighbour_dfs = []
            for nId in range(len(neighbours)):
                if nId == 0:
                    pass
                else:
                    neighbour_dfs.append(utils.get_gsdr(neighbours[nId], paths[nId]))
            # get matching stats for nearest gauge and offset calculateAffinityIndexAndPearson(ts1, ts2) -> returns a flag

            # do neighbour check

            # filter out gauges with AI < 0.9
            neighbour_dfs2 = []
            for ndf in neighbour_dfs:
                nai, nr2, nf = utils.calculate_affinity_index_and_pearson(ts0.to_frame("ts1"), ndf)
                if nai > 0.9:
                    neighbour_dfs2.append(ndf)
                else:
                    pass

            flags_df = utils.check_neighbours(ts0.to_frame("ts1"), neighbour_dfs2, self.series.station_id, 'hourly')

            flags_dates = list(flags_df.index.values)
            flags_vals = list(flags_df)

            # do neighbour check for dry periods and flag the whole 15 day period
            dry_flags_df = utils.check_neighbours_dry(ts0.to_frame("ts1"), neighbour_dfs2)
            dry_flags_dates = list(dry_flags_df.index.values)
            dry_flags_vals = list(dry_flags_df)

            i1 = []
            i2 = []
            i3 = []

            for i in range(len(dry_flags_vals)):
                if dry_flags_vals[i] == 1:
                    for j in range(15):
                        i1.append(i - j)
                elif dry_flags_vals[i] == 2:
                    for j in range(15):
                        i2.append(i - j)
                elif dry_flags_vals[i] == 3:
                    for j in range(15):
                        i3.append(i - j)
                else:
                    pass

            for i in i1:
                dry_flags_vals[i] = 1
            for i in i2:
                dry_flags_vals[i] = 2
            for i in i3:
                dry_flags_vals[i] = 3

            # add daily flags back onto hourly
            flags_dt = [datetime(d.year, d.month, d.day, 7) for d in flags_dates]
            flags_df = pd.Series(flags_vals, index=flags_dt).to_frame("flags")
            dry_flags_dt = [datetime(d.year, d.month, d.day, 7) for d in dry_flags_dates]
            dry_flags_df = pd.Series(dry_flags_vals, index=dry_flags_dt).to_frame("dryFlags")

            df = pd.concat([df, flags_df, dry_flags_df], axis=1, join_axes=[df.index])
            df.flags = df.flags.fillna(method="ffill", limit=23)
            df.dryFlags = df.dryFlags.fillna(method="ffill", limit=23)
            df.fillna(-999, inplace=True)

            return [list(df.flags.astype(int)), list(df.dryFlags.astype(int))]

        else:
            tmp = [-999 for i in range(df['roll'].shape[0])]
            return [tmp, tmp]


    # ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    def check_daily_neighbours(self):
        df = self.series.data.to_frame("target")
        # convert hourly to daily 7am-7am
        df["roll"] = np.around(df.target.rolling(window=24, center=False, min_periods=24).sum(), 1)
        dfd = df[df.index.hour == 7]
        dts = list(dfd.index)
        daily_vals = list(dfd.roll)

        # offset by one day in either direction
        dtsm1 = []
        dts0 = []
        dtsp1 = []
        for dt in dts:
            sm1 = dt - timedelta(days=2)
            s0 = dt - timedelta(days=1)
            sp1 = dt

            dtsm1.append(date(sm1.year, sm1.month, sm1.day))
            dts0.append(date(s0.year, s0.month, s0.day))
            dtsp1.append(date(sp1.year, sp1.month, sp1.day))

        tsm1 = pd.Series(daily_vals, index=dtsm1)
        ts0 = pd.Series(daily_vals, index=dts0)
        tsp1 = pd.Series(daily_vals, index=dtsp1)

        # find neighbours
        neighbours = self.find_daily_neighbours()

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
            neighbour_dfs = []
            for nId in neighbours:
                neighbour_start_year = self.daily_dates[self.daily_names.index(nId)][0].year
                neighbour_end_year = self.daily_dates[self.daily_names.index(nId)][1].year
                neighbour_dfs.append(utils.get_gpcc(neighbour_start_year, neighbour_end_year, nId))

            # get matching stats for nearest gauge and offset calculateAffinityIndexAndPearson(ts1, ts2) -> returns a flag
            nearest = neighbour_dfs[0].rename(columns={"GPCC": "ts2"})
            sm1ai, sm1r2, sm1f = utils.calculate_affinity_index_and_pearson(tsm1.to_frame("ts1"), nearest)
            s0ai, s0r2, s0f = utils.calculate_affinity_index_and_pearson(ts0.to_frame("ts1"), nearest)
            sp1ai, sp1r2, sp1f = utils.calculate_affinity_index_and_pearson(tsp1.to_frame("ts1"), nearest)

            ais = [sm1ai, s0ai, sp1ai]
            r2s = [sm1r2, s0r2, sp1r2]

            if ais.index(max(ais)) == r2s.index(max(r2s)):
                offset_flag = ais.index(max(ais)) - 1
            else:
                offset_flag = 0

            # do neighbour check
            # print("doing neighbour check")

            # dp 29/11/2019 - check that there is indeed some overlap between the hourly and GPCC
            # daily gauge - for DE_02483 one neighbour (3798) ends in 1972 in the data file but
            # statlex_daily says it continues until 2018, which results in no overlap and
            # a divide by zero error when trying to calculate the percentage matching
            # - for now check placed in calculate AI etc function

            # filter out gauges with AI < 0.9
            neighbour_dfs2 = []
            for nId, ndf in zip(neighbours, neighbour_dfs):
                ndf2 = ndf.rename(columns={"GPCC": "ts2"})
                nai, nr2, nf = utils.calculate_affinity_index_and_pearson(ts0.to_frame("ts1"), ndf2)
                if nai > 0.9:
                    neighbour_dfs2.append(ndf)
                else:
                    pass

            flags_df = utils.check_neighbours(ts0.to_frame("ts1"), neighbour_dfs2, self.series.station_id, 'daily')
            flags_dates = list(flags_df.index.values)
            flags_vals = list(flags_df)

            # do neighbour check for dry periods and flag the whole 15 day period
            dry_flags_df = utils.check_neighbours_dry(ts0.to_frame("ts1"), neighbour_dfs2)
            dry_flags_dates = list(dry_flags_df.index.values)
            dry_flags_vals = list(dry_flags_df)

            i1 = []
            i2 = []
            i3 = []

            for i in range(len(dry_flags_vals)):
                if dry_flags_vals[i] == 1:
                    for j in range(15):
                        i1.append(i - j)
                elif dry_flags_vals[i] == 2:
                    for j in range(15):
                        i2.append(i - j)
                elif dry_flags_vals[i] == 3:
                    for j in range(15):
                        i3.append(i - j)
                else:
                    pass

            for i in i1:
                dry_flags_vals[i] = 1
            for i in i2:
                dry_flags_vals[i] = 2
            for i in i3:
                dry_flags_vals[i] = 3

            # add daily flags back onto hourly
            flags_dt = [datetime(d.year, d.month, d.day, 7) for d in flags_dates]
            flags_df = pd.Series(flags_vals, index=flags_dt).to_frame("flags")
            dry_flags_dt = [datetime(d.year, d.month, d.day, 7) for d in dry_flags_dates]
            dry_flags_df = pd.Series(dry_flags_vals, index=dry_flags_dt).to_frame("dryFlags")

            df = pd.concat([df, flags_df, dry_flags_df], axis=1, join_axes=[df.index])
            df.flags = df.flags.fillna(method="ffill", limit=23)
            df.dryFlags = df.dryFlags.fillna(method="ffill", limit=23)
            df.fillna(-999, inplace=True)
            return [list(df.flags.astype(int)), offset_flag, s0ai, s0r2, s0f, list(df.dryFlags.astype(int))]

        # -999 if no neighbours
        else:
            tmp = [-999 for i in range(df['roll'].shape[0])]
            return [tmp, -999, -999, -999, -999, tmp]


    # ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++



    # ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    def check_monthly_neighbours(self):  # Hey Liz! check this when you have access to monthly, esp mapping back onto hourly.
        df = self.series.data.to_frame("target")

        # convert hourly to daily 7am-7am
        dfm = df.resample("M", label='right', closed='right').apply(lambda x: x.values.sum())
        # find neighbours
        neighbours = self.find_monthly_neighbours()

        # Check for duplicate neighbours
        if neighbours is not None:
            tmp = []
            for n in neighbours:
                if n not in tmp:
                    tmp.append(n)
            neighbours = tmp.copy()

        if neighbours is None:
            hourly_flags_s = df.copy()
            hourly_flags_s['flags'] = -999
            hourly_factor_flags_s = df.copy()
            hourly_factor_flags_s['factor_flags'] = -999
        else:

            # get gpcc
            neighbour_dfs = []
            for n_id in neighbours:
                neighbour_start_year = self.monthly_dates[self.monthly_names.index(n_id)][0].year
                neighbour_end_year = self.monthly_dates[self.monthly_names.index(n_id)][1].year
                neighbour_dfs.append(utils.get_monthly_gpcc(neighbour_start_year, neighbour_end_year, n_id))
            # get matching stats for nearest gauge and offset calculateAffinityIndexAndPearson(ts1, ts2) -> returns a flag

            # do neighbour check

            flags_df, factor_flags_df = utils.check_m_neighbours(dfm, neighbour_dfs)

            # set dates to be at 2300 (rather than 0000) so bfill works
            flags_df.index += timedelta(hours=23)
            factor_flags_df.index += timedelta(hours=23)

            orig_dates = list(df.index.values)
            hourly_flags_s = flags_df.reindex(orig_dates, method="bfill")
            hourly_factor_flags_s = factor_flags_df.reindex(orig_dates, method="bfill")

            # count valid values within month and set flag as nan if more than 5% of data is missing
            # - hourly percentage differences
            hourly_flags_s = hourly_flags_s.to_frame()
            hourly_flags_s['count'] = hourly_flags_s.groupby(
                [hourly_flags_s.index.year, hourly_flags_s.index.month]).transform('count')
            hourly_flags_s['expected'] = hourly_flags_s.index.days_in_month * 24
            hourly_flags_s['frac_complete'] = hourly_flags_s['count'] / hourly_flags_s['expected']
            hourly_flags_s.loc[hourly_flags_s['frac_complete'] < 0.95, 'flags'] = np.nan
            hourly_flags_s.drop(['count', 'expected', 'frac_complete'], axis=1, inplace=True)
            # - hourly factor differences
            hourly_factor_flags_s = hourly_factor_flags_s.to_frame()
            hourly_factor_flags_s['count'] = hourly_factor_flags_s.groupby(
                [hourly_factor_flags_s.index.year, hourly_factor_flags_s.index.month]).transform('count')
            hourly_factor_flags_s['expected'] = hourly_factor_flags_s.index.days_in_month * 24
            hourly_factor_flags_s['frac_complete'] = hourly_factor_flags_s['count'] / hourly_factor_flags_s['expected']
            hourly_factor_flags_s.loc[hourly_factor_flags_s['frac_complete'] < 0.95, 'factor_flags'] = np.nan
            hourly_factor_flags_s.drop(['count', 'expected', 'frac_complete'], axis=1, inplace=True)

            hourly_flags_s.fillna(-999, inplace=True)
            hourly_factor_flags_s.fillna(-999, inplace=True)

        return [list(hourly_flags_s['flags'].astype(int)), list(hourly_factor_flags_s['factor_flags'].astype(int))]


    # +++++++++++++++++++++++++++++++ MAIN FUNCTION, CALLS CHECKS ++++++++++++++++++++++++++++++++


    def get_flags(self):  # pass intense object

        # Ensure non-nan lat/lon before neighbour checks (issue for some Sicily stations)
        if np.isfinite(self.series.latitude) and np.isfinite(self.series.longitude):
            self.hourly_neighbours, self.hourly_neighbours_dry = self.check_hourly_neighbours()
            if self.use_daily_neighbours:
                self.daily_neighbours, self.offset, self.preQC_affinity_index, self.preQC_pearson_coefficient, self.factor_daily, self.daily_neighbours_dry = self.check_daily_neighbours()
            if self.use_monthly_neighbours:
                self.monthly_neighbours, self.factor_monthly = self.check_monthly_neighbours()

        self.world_record = self.world_record_check_ts()

        self.Rx1day = self.rx1day_check_ts()

        self.CDD = self.cdd_check()

        self.daily_accumualtions = self.daily_accums_check()

        self.monthly_accumulations = self.monthly_accums_check()

        self.streaks = self.streaks_check()

        self.percentiles = self.check_percentiles()

        self.k_largest = self.check_k_largest()

        self.days_of_week = self.check_days_of_week()

        self.hours_of_day = self.check_hours_of_day()

        self.intermittency = self.check_intermittency()

        self.breakpoint = self.check_break_point()

        self.R99pTOT = self.r99ptot_check_annual()

        self.PRCPTOT = self.prcptot_check_annual()

        self.change_min_value = self.change_in_min_val_check()

        return self

    def write(self, directory):
        if not os.path.exists(directory):
            os.mkdir(directory)
        with open(os.path.join(directory, self.series.station_id) + '_QC.txt', 'w') as o:
            o.write(
                "Station ID: {self.series.station_id}\n"
                "Country: {self.series.country}\n"
                "Original Station Number: {self.series.original_station_number}\n"
                "Original Station Name: {self.series.original_station_name}\n"
                "Path to original data: {self.series.path_to_original_data}\n"
                "Latitude: {self.series.latitude}\n"
                "Longitude: {self.series.longitude}\n"
                "Start datetime: {self.series.start_datetime:%Y%m%d%H}\n"
                "End datetime: {self.series.end_datetime:%Y%m%d%H}\n"
                "Elevation: {self.series.elevation}\n"
                "Number of records: {self.series.number_of_records}\n"
                "Percent missing data: {self.series.percent_missing_data:.2f}\n"
                "Original Timestep: {self.series.original_timestep}\n"
                "New Timestep: {self.series.new_timestep}\n"
                "Original Units: {self.series.original_units}\n"
                "New Units: {self.series.new_units}\n"
                "Time Zone: {self.series.time_zone}\n"
                "Daylight Saving info: {self.series.daylight_saving_info}\n"
                "No data value: {self.series.no_data_value}\n"
                "Resolution: {self.series.resolution:.2f}\n"
                "Other: {self.series.other}\n"
                "Years where Q95 equals 0: {self.percentiles[0]}\n"
                "Years where Q99 equals 0: {self.percentiles[1]}\n"
                "Years where k1 equals 0: {self.k_largest[0]}\n"
                "Years where k5 equals 0: {self.k_largest[1]}\n"
                "Years where k10 equals 0: {self.k_largest[2]}\n"
                "Uneven distribution of rain over days of the week: {self.days_of_week}\n"
                "Uneven distribution of rain over hours of the day: {self.hours_of_day}\n"
                "Years with intermittency issues: {self.intermittency}\n"
                "Break point detected: {self.breakpoint}\n"
                "R99pTOT checks: {self.R99pTOT}\n"
                "PRCPTOT checks: {self.PRCPTOT}\n"
                "Years where min value changes: {self.change_min_value}\n"
                "Optimum offset: {self.offset}\n"
                "Pre QC Affinity Index: {self.preQC_affinity_index}\n"
                "Pre QC Pearson coefficient: {self.preQC_pearson_coefficient}\n"
                "Factor against nearest daily gauge: {self.factor_daily}\n".format(self=self))

            empty_series = np.full(len(self.series.data), self.series.no_data_value, dtype=int)

            if self.hourly_neighbours is None:
                self.hourly_neighbours = empty_series

            if self.hourly_neighbours_dry is None:
                self.hourly_neighbours_dry = empty_series

            if self.daily_neighbours is None:
                self.daily_neighbours = empty_series

            if self.daily_neighbours_dry is None:
                self.daily_neighbours_dry = np.full(len(self.series.data), self.series.no_data_value, dtype=int)

            if self.monthly_neighbours is None:
                self.monthly_neighbours = np.full(len(self.series.data), self.series.no_data_value, dtype=int)

            if self.world_record is None:
                self.world_record = empty_series

            if self.Rx1day is None:
                self.Rx1day = empty_series

            if self.CWD is None:
                self.CWD = empty_series

            if self.CDD is None:
                self.CDD = empty_series

            if self.daily_accumualtions is None:
                self.daily_accumualtions = empty_series

            if self.monthly_accumulations is None:
                self.monthly_accumulations = empty_series

            if self.streaks is None:
                self.streaks = empty_series

            if self.factor_monthly is None:
                self.factor_monthly = empty_series

            self.series.data.fillna(self.series.no_data_value, inplace=True)
            vals_flags = zip([float(format(v, '.3f')) for v in self.series.data.values],
                             self.hourly_neighbours,
                             self.hourly_neighbours_dry,
                             self.daily_neighbours,
                             self.daily_neighbours_dry,
                             self.monthly_neighbours,
                             self.world_record,
                             self.Rx1day,
                             self.CWD,
                             self.CDD,
                             self.daily_accumualtions,
                             self.monthly_accumulations,
                             self.streaks,
                             self.factor_monthly)
            print(vals_flags)
            o.writelines(str(a)[1:-1] + "\n" for a in vals_flags)


def read_intense_qc(path, only_metadata=False, opened=False):
    metadata = []
    if not opened:
        try:
            with open(path, 'rb') as f:
                while True:
                    try:
                        key, val = f.readline().strip().split(':', maxsplit=1)
                        key = key.lower()
                        metadata.append((key.strip(), val.strip()))
                    except:
                        key = "other"
                        val = ""
                    if 'factor against nearest daily gauge' in metadata[-1][0].lower():
                        break
                if only_metadata:
                    data = None
                else:
                    data = f.readlines()
        except:
            with open(path, 'r') as f:
                while True:
                    try:
                        key, val = f.readline().strip().split(':', maxsplit=1)
                        key = key.lower()
                        metadata.append((key.strip(), val.strip()))
                    except:
                        key = "other"
                        val = ""
                    if 'factor against nearest daily gauge' in metadata[-1][0].lower():
                        break
                if only_metadata:
                    data = None
                else:
                    data = f.readlines()

    else:
        f = path
        while True:
            try:
                key, val = str(f.readline().strip())[2:-1].split(':', maxsplit=1)
                key = key.lower()
                metadata.append((key.strip(), val.strip()))
            except:
                key = "other"
                val = ""
            if 'factor against nearest daily gauge' in metadata[-1][0].lower():
                break
        if only_metadata:
            data = None
        else:
            data = f.readlines()
    metadata = dict(metadata)

    for variable in ['country', 'elevation', 'time zone', 'daylight saving info', 'original station name',
                     'original station number']:
        if variable not in metadata.keys():
            metadata[variable] = 'NA'
    if data is not None:
        try:
            data = [i.rstrip().split(", ") for i in data]
        except:
            # working on files written from linux (DWD server), it seems to work
            # without specifying "utf-8" as argument for decode...
            data = [i.rstrip().decode().split(", ") for i in data]

        data = np.array(data)
        data = pd.DataFrame(data, pd.date_range(start=datetime.strptime(metadata['start datetime'], '%Y%m%d%H'),
                                                end=datetime.strptime(metadata['end datetime'], '%Y%m%d%H'),
                                                freq=metadata['new timestep'][:-2] + 'H'), dtype=float,
                            columns=["vals", "hourly_neighbours", "hourly_neighbours_dry", "daily_neighbours",
                                     "daily_neighbours_dry", "monthly_neighbours", "world_record", "Rx1day",
                                     "CWD", "CDD", "daily_accumualtions", "monthly_accumulations",
                                     "streaks", "factor_monthly"])

        data = data.where(data != -999)

    s = Series(station_id=metadata['station id'],
               path_to_original_data=metadata['path to original data'],
               latitude=try_float(metadata['latitude']),
               longitude=try_float(metadata['longitude']),
               original_timestep=metadata['original timestep'],
               original_units=metadata['original units'],
               new_units=metadata['new units'],
               new_timestep=metadata['new timestep'],
               data=data.vals,
               elevation=metadata['elevation'],
               country=metadata['country'],
               original_station_number=metadata['original station number'],
               original_station_name=metadata['original station name'],
               time_zone=metadata['time zone'])

    tmp = metadata['years where min value changes']
    change_flag = try_int(tmp.split(", ")[0][1:])
    if change_flag == 0:
        change_list = [np.nan]
    elif change_flag == 1:
        years = tmp[5:-2]
        years = years.split(", ")
        change_list = [int(y) for y in years]
    
    qc = Qc(
        series=s,
        percentiles=[try_list(metadata['years where q95 equals 0']), try_list(metadata['years where q99 equals 0'])],
        k_largest=[try_list(metadata['years where k1 equals 0']), try_list(metadata['years where k5 equals 0']),
                     try_list(metadata['years where k10 equals 0'])],
        days_of_week=try_int(metadata['uneven distribution of rain over days of the week']),
        hours_of_day=try_int(metadata['uneven distribution of rain over hours of the day']),
        intermittency=try_list(metadata['years with intermittency issues']),
        breakpoint=try_int(metadata['break point detected']),
        R99pTOT=try_list(metadata['r99ptot checks']),
        PRCPTOT=try_list(metadata['prcptot checks']),
        change_min_value=[change_flag, change_list],
        offset=try_int(metadata['optimum offset']),
        preQC_affinity_index=try_float(metadata['pre qc affinity index']),
        preQC_pearson_coefficient=try_float(metadata['pre qc pearson coefficient']),
        factor_daily=try_float(metadata['factor against nearest daily gauge']),

        hourly_neighbours=data.hourly_neighbours,
        hourly_neighbours_dry=data.hourly_neighbours_dry,
        daily_neighbours=data.daily_neighbours,
        daily_neighbours_dry=data.daily_neighbours_dry,
        monthly_neighbours=data.monthly_neighbours,
        world_record=data.world_record,
        Rx1day=data.Rx1day,
        CWD=data.CWD,
        CDD=data.CDD,
        daily_accumualtions=data.daily_accumualtions,
        monthly_accumulations=data.monthly_accumulations,
        streaks=data.streaks,
        factor_monthly=data.factor_monthly
        
    )
    
    return qc