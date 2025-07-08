# -*- coding: utf-8 -*-
################################
# File Name   : dataCollector.py
# Author      : zhangjingwen.silvia
# Created On  : 2025-02-13 17:59:37
# Description :
################################
import datetime
import logging
import os
import re
import sqlite3
import sys
import argparse
import traceback
from hashlib import sha1

import numpy as np
import pandas as pd

sys.path.append(str(os.environ['MEM_PREDICTION_INSTALL_PATH']))
from common import common, common_es
from config import config

logger = common.get_logger(level=logging.DEBUG)


def read_args():
    """
    Read in arguments.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument('--job',
                        action='store_true',
                        help='Save job to ES job* tables.')
    parser.add_argument('--summary',
                        action='store_true',
                        help='Save job to ES summary* tables.')
    parser.add_argument('-f', '--file',
                        default='',
                        type=str,
                        help='Data that will be saved.')

    args = parser.parse_args()

    return args


class DataCollector:
    def __init__(self):
        self.config_dic = common_es.read_conf('web_app.yaml').get('DataCollector')
        self.es_db = common_es.ESDB()

        self.data_format = config.job_format.lower() if hasattr(config, 'job_format') else 'csv'

        if self.es_db.ep_client is None:
            self.es_db.create_ep_client()

    def save_job(self, data_file: str) -> bool:
        """
        Job Saving.
        """
        logger.info('Save job data.')

        if not os.path.exists(data_file):
            logger.error(f'File does not exists: {data_file}')
            return False

        ori_df = self.read_file(data_file=data_file)
        year = self.extract_file_year(file_name=os.path.basename(data_file))
        summary_date = self.extract_file_date(os.path.basename(data_file))
        df = self.extract_job_data(year=year, df=ori_df, summary_date=summary_date)
        name = f'job_{self.extract_file_date(os.path.basename(data_file))}'
        ret = self.es_db.save_data(name, df)
        return ret

    def save_summary(self, data_file: str) -> bool:
        """
        Summary Saving.
        """
        logger.info('Save summary data.')

        if not os.path.exists(data_file):
            logger.error(f'File does not exists: {data_file}')
            return False

        ori_df = self.read_file(data_file=data_file)
        year = self.extract_file_year(file_name=os.path.basename(data_file))
        summary_date = self.extract_file_date(os.path.basename(data_file))
        df = self.extract_summary_data(year=year, df=ori_df, summary_date=summary_date)
        name = f'summary_{self.extract_file_date(os.path.basename(data_file))}'
        ret = self.es_db.save_data(name, df)

        return ret

    def extract_job_data(self, year: str, df: pd.DataFrame, summary_date: str) -> pd.DataFrame:
        df = self.basic_data_process(year, df)
        df['date'] = summary_date
        df['date'] = pd.to_datetime(df['date'], format='%Y_%m_%d').dt.strftime('%Y-%m-%d')
        return df

    def extract_summary_data(self, year: str, df: pd.DataFrame, summary_date: str) -> pd.DataFrame:
        df = self.basic_data_process(year, df)
        df = self.aggregation_data_process(df)
        df['date'] = summary_date
        df['date'] = pd.to_datetime(df['date'], format='%Y_%m_%d').dt.strftime('%Y-%m-%d')
        return df

    @staticmethod
    def basic_data_process(year: str, df: pd.DataFrame) -> pd.DataFrame:
        # job id
        df['job_id'] = pd.to_numeric(df['job_id'], errors='coerce')
        df.dropna(subset=['job_id', 'started_time', 'finished_time'], inplace=True)
        df['job_id'] = df['job_id'].astype(int)

        df['started_time'] = pd.to_datetime(df['started_time'].apply(lambda x: f'{x} {year}'), format='%a %b %d %H:%M:%S %Y', errors='coerce')
        df['finished_time'] = pd.to_datetime(df['finished_time'].apply(lambda x: f'{x} {year}'), format='%a %b %d %H:%M:%S %Y', errors='coerce')

        df['started_time'] = df.apply(
            lambda row: row['started_time'].replace(year=row['started_time'].year - 1)
            if row['finished_time'] < row['started_time'] else row['started_time'],
            axis=1
        )
        df['started_time'] = df['started_time'].apply(lambda x: x.timestamp() if pd.notnull(x) else np.nan).astype('Int64')
        df['finished_time'] = df['finished_time'].apply(lambda x: x.timestamp() if pd.notnull(x) else np.nan).astype('Int64')
        df.dropna(subset=['started_time', 'finished_time'], inplace=True)

        # run time
        df['run_time'] = df['finished_time'] - df['started_time']

        # job name
        df['job_name'] = df['job_name'].fillna('None')

        # job user
        df['user'] = df['user'].fillna('None')

        # status
        df = df.loc[df['status'].isin(['DONE', 'EXIT'])]

        # project
        df['project'] = df['project'].fillna('None')

        # queue
        df['queue'] = df['queue'].fillna('None')

        # cwd
        df['cwd'] = df['cwd'].fillna('None')

        # command
        df['command'] = df['command'].fillna('None')

        # rusage memory
        df['rusage_mem'] = df['rusage_mem'].fillna(0)
        df['rusage_mem'] = df['rusage_mem'].replace('', 0)
        df['rusage_mem'] = df['rusage_mem'].astype(float).astype(int)

        # max memory
        df['max_mem'] = df['max_mem'].fillna(0)
        df['max_mem'] = df['max_mem'].replace('', 0)
        df['max_mem'] = df['max_mem'].astype(float).astype(int)

        # avg memory
        df['avg_mem'] = df['avg_mem'].fillna(0)
        df['avg_mem'] = df['avg_mem'].replace('', 0)
        df['avg_mem'] = df['avg_mem'].astype(float).astype(int)

        # job_description
        df['job_description'] = df['job_description'].fillna('None')

        # job interactive mode
        df['interactive_mode'] = df['interactive_mode'].replace('True', True).replace('False', False)
        df = df.loc[df['interactive_mode'].isin([True, False])]

        # cpu time
        df['cpu_time'] = df['cpu_time'].fillna(0)
        df['cpu_time'] = df['cpu_time'].replace('', 0)
        df['cpu_time'] = df['cpu_time'].astype(float).astype(int)

        # span hosts
        df['span_hosts'] = df['span_hosts'].fillna(0)
        df['span_hosts'] = df['span_hosts'].replace('', 0)
        df['span_hosts'] = df['span_hosts'].astype(float).astype(int)

        # processors_requested
        df['processors_requested'] = df['processors_requested'].fillna(1)
        df['processors_requested'] = df['processors_requested'].astype(float).astype(int)

        # cpu_utilization
        df['cpu_utilization'] = df['cpu_time'] / (df['run_time'] * df['processors_requested'])
        df['cpu_utilization'] = df['cpu_utilization'].replace([np.inf, -np.inf], 0)
        df['cpu_utilization'] = df['cpu_utilization'].fillna(0)

        return df

    @staticmethod
    def aggregation_data_process(df: pd.DataFrame) -> pd.DataFrame:
        user_df = pd.DataFrame()

        # pre-precess: excess / insufficient memory quantity, excess cpu quantity
        df['excess_mem_quantity'] = ((df['rusage_mem'] - df['max_mem']).apply(lambda x: 0 if x < 0 else x)) * df['run_time'] / 3600
        df['insufficient_mem_quantity'] = ((df['max_mem'] - df['rusage_mem']).apply(lambda x: 0 if x < 0 else x)) * df['run_time'] / 3600
        df['excess_mem_quantity'] = df['excess_mem_quantity'].astype(int)
        df['insufficient_mem_quantity'] = df['insufficient_mem_quantity'].astype(int)

        df['excess_cpu_quantity'] = df['cpu_utilization'].apply(lambda x: max(x - 1, 0))
        df['excess_cpu_quantity'] = df['excess_cpu_quantity'].fillna(0)
        df['excess_cpu_quantity'] = df['excess_cpu_quantity'].replace([np.inf, -np.inf], 0)
        df['excess_cpu_quantity'] = df['excess_cpu_quantity'].astype(int)

        # excess memory quantity: TB*H
        user_df['excess_mem_quantity'] = (df["excess_mem_quantity"].groupby(df['user']).sum() / (1024 * 1024)).round().astype(int)

        # insufficient memory quantity: TB*H
        user_df['insufficient_mem_quantity'] = (df['insufficient_mem_quantity'].groupby(df['user']).sum() / (1024 * 1024)).round().astype(int)

        # job num
        user_df['job_num'] = df['job_id'].groupby(df['user']).count()

        # job duration average
        user_df['job_duration_sum'] = (df['run_time'].groupby(df['user']).sum()) / 3600

        # max memory average: GB
        user_df['max_mem_sum'] = (df['max_mem'].groupby(df['user']).sum() / 1024).round().astype(int)

        # reservation memory average: GB
        user_df['rusage_mem_sum'] = (df['rusage_mem'].groupby(df['user']).sum() / 1024).round().astype(int)

        # memory -> 95%
        user_df['95_quantile_mem'] = (df["max_mem"].groupby(df["user"]).quantile(0.95) / 1024).round().astype(int)

        # excess cpu quantity
        user_df['excess_cpu_quantity'] = df['excess_cpu_quantity'].groupby(df['user']).sum()

        user_df['user'] = user_df.index

        return user_df

    def read_file(self, data_file: str) -> pd.DataFrame:
        """
        Reading data from original file.
        """
        if self.data_format == 'csv':
            df = pd.read_csv(data_file)
            df.rename(columns={'index': 'job_id'}, inplace=True)
        elif self.data_format == 'json':
            df = pd.read_csv(data_file)
        elif self.data_format == 'sqlite':
            conn = sqlite3.connect(data_file)
            query = 'SELECT * FROM job'
            df = pd.read_sql_query(query, conn)
            df.rename(columns={'job': 'job_id'}, inplace=True)
        else:
            logger.error('Could not find data format, please check dataCollector.yaml!')
            raise RuntimeError

        df['_id'] = df.apply(lambda x: self.set_id(x, column_list=['job_id', 'started_time']), axis=1)
        df = df.drop_duplicates(subset='_id', keep='first')
        df.set_index('_id', inplace=True)

        return df

    @staticmethod
    def set_id(row, column_list: list):
        try:
            id_str = '-'.join([str(row[col]) for col in column_list])
            sha1obj = sha1()
            sha1obj.update(id_str.encode('utf-8'))
            return sha1obj.hexdigest()
        except Exception as error:
            logging.debug(f'Set id failed. Error: {str(error)}, Stack: {traceback.format_exc()}')
            return ''

    @staticmethod
    def extract_file_date(file_name: str) -> str:
        """
        Extracting the date from original file name.
        """
        pattern = r'(\d{4})[-/\.|_]?(\d{2})[-/\.|_]?(\d{2})'
        match = re.search(pattern, file_name)

        if match:
            year, month, day = match.groups()
            formatted_date = f"{year}_{month}_{day}"
            return formatted_date

        logger.error(f'Invalid file name: {file_name}')
        raise RuntimeError

    @staticmethod
    def extract_file_year(file_name: str) -> str:
        """
        Extracting the date from original file name.
        """
        pattern = r'(\d{4})[-/\.]?(\d{2})[-/\.]?(\d{2})'
        match = re.search(pattern, file_name)

        if match:
            year, _, _ = match.groups()
            return year

        logger.error(f'Could not find year: {file_name}')
        return str(datetime.datetime.now().year)


################
# Main Process #
################
def main():
    ret = False

    try:
        args = read_args()
        data_collector = DataCollector()

        if args.job:
            ret = data_collector.save_job(data_file=args.file)
        elif args.summary:
            ret = data_collector.save_summary(data_file=args.file)
    except Exception as error:
        logger.error(str(error))
        logger.debug(traceback.format_exc())
    finally:
        if ret:
            logger.info('Save data successfully!')
        else:
            logger.info('Save data failed.')


if __name__ == '__main__':
    main()
