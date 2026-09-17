# ruff: noqa
import heapq
import math
import os
import sys
from collections import defaultdict

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from elasticsearch_dsl import Search, A

sys.path.append(str(os.environ['MEM_PREDICTION_INSTALL_PATH']))
from common.common_es import ESDB

from elasticsearch.helpers import scan


class DataProcessor:
    def __init__(self):
        self.es_db = ESDB()
        self.es_db.create_es_client()

    def analyze_server_needs(self, start_date, end_date, window_sec=3600):
        s = Search(using=self.es_db.es_client, index='job_*')
        s = s.query("range", date={"gte": start_date, "lte": end_date, "format": "yyyy-MM-dd"})

        results = scan(self.es_db.es_client, query=s.to_dict(), size=1000)

        delta = defaultdict(list)

        timestamps = []

        for hit in results:
            job = hit['_source']
            try:
                start_time = int(job['started_time'])
                end_time = int(job['finished_time'])
                max_mem = min(math.ceil(float(job['max_mem'] * 2.5) / 1024), (4096 * 0.8))  # GB
            except KeyError:
                continue

            start_bucket = (start_time // window_sec) * window_sec
            end_bucket = math.ceil(end_time / window_sec) * window_sec

            delta[start_bucket].append(('start', max_mem))
            delta[end_bucket].append(('end', max_mem))

            timestamps.append(start_bucket)
            timestamps.append(end_bucket)

        timestamps = sorted(set(timestamps))
        if not timestamps:
            print("无有效数据")
            return

        min_time = timestamps[0]
        max_time = timestamps[-1]
        time_series = list(range(min_time, max_time + window_sec, window_sec))

        active_jobs = []
        results = []

        print("开始时间序列分析，总窗口数：", len(time_series))

        for ts in time_series:
            events = delta.get(ts, [])
            for event_type, mem in events:
                if event_type == 'start':
                    heapq.heappush(active_jobs, (-mem, mem))  # 用大顶堆，方便 FFD
                else:
                    try:
                        active_jobs.remove((-mem, mem))  # O(N)，如果慢可以用 Counter 或更优结构
                    except ValueError:
                        pass
            heapq.heapify(active_jobs)

            active_mem_list = [mem for (_, mem) in active_jobs]
            n_500, n_2t, n_4t = self.ffd_pack(active_mem_list)

            results.append([ts, n_500, n_2t, n_4t])

        df = pd.DataFrame(results, columns=['timestamp', 'n_500', 'n_2t', 'n_4t'])
        df.to_csv('server_needs_8_9.csv', index=False)
        print("分析完成，结果已保存为 server_needs.csv")

        # 画图
        """
        plt.figure()
        plt.plot(df['timestamp'], df['n_500'], label='500G')
        plt.plot(df['timestamp'], df['n_1t'], label='1T')
        plt.plot(df['timestamp'], df['n_2t'], label='2T')
        plt.plot(df['timestamp'], df['n_4t'], label='4T')
        plt.xlabel('Epoch Time')
        plt.ylabel('Number of Servers')
        plt.legend()
        plt.title('Server Demand Over Time')
        plt.show()
        """

        # 输出比例
        p95_500 = math.ceil(np.percentile(df['n_500'], 95) * 1.15)
        p95_2t = math.ceil(np.percentile(df['n_2t'], 95) * 1.15)
        # p95_2t = math.ceil(np.percentile(df['n_2t'], 95) * 1.15)
        p95_4t = math.ceil(np.percentile(df['n_4t'], 95) * 1.15)

        total = p95_500 + p95_2t + p95_4t

        print("\n推荐采购量（P95 + 15% buffer）：")
        print(f"500G: {p95_500}")
        print(f"2T:   {p95_2t}")
        # print(f"2T:   {p95_2t}")
        print(f"4T:   {p95_4t}")
        print(f"500G: {p95_500} ({p95_500 / total:.3%})")
        print(f"2T:   {p95_2t} ({p95_2t / total:.3%})")
        # print(f"2T:   {p95_2t} ({p95_2t / total:.3%})")
        print(f"4T:   {p95_4t} ({p95_4t / total:.3%})")

    def cpu_slots_mem_ratio(self, start_date, end_date, window_sec=3600):
        """
        使用 elasticsearch_dsl.Search 结构，统计 500/1T/2T/4T 内存档的 CPU slots / Mem-GB 分布
        并生成并发时间序列
        """
        s = Search(using=self.es_db.es_client, index='job_*')
        s = s.query("range", date={"gte": start_date, "lte": end_date, "format": "yyyy-MM-dd"})
        s = s.source(['started_time', 'finished_time', 'max_mem', 'processors_requested'])

        results = scan(self.es_db.es_client, query=s.to_dict(), size=1000, scroll='5m')

        delta = defaultdict(list)
        records = []

        for hit in results:
            job = hit['_source']
            try:
                start_time = int(job['started_time'])
                end_time = int(job['finished_time'])
                max_mem = float(job['max_mem']) / (1024 ** 2)  # MB -> TB
                slots = int(job['processors_requested'])
            except (KeyError, ValueError):
                continue

            if max_mem > 0:
                records.append((max_mem, slots))

            bucket_start = (start_time // window_sec) * window_sec
            bucket_end = math.ceil(end_time / window_sec) * window_sec

            delta[bucket_start].append(("start", max_mem, slots))
            delta[bucket_end].append(("end", max_mem, slots))

        df = pd.DataFrame(records, columns=["mem_tb", "cpu_slots"])
        df.to_csv('cpu_sna.csv')
        df["mem_bin"] = pd.cut(
            df["mem_tb"],
            bins=[0, 0.5, 1, 2, 4, np.inf],
            labels=["≤500", "500-1T", "1-2T", "2-4T", ">4T"],
            right=True, include_lowest=True
        )
        df["slots_per_tb"] = df["cpu_slots"] / df["mem_tb"]

        import matplotlib.pyplot as plt
        import seaborn as sns

        summary = (
            df.groupby("mem_bin")["slots_per_tb"]
            .agg(avg="mean", p50=lambda x: x.quantile(0.5),
                 p90=lambda x: x.quantile(0.9), count="count")
            .reset_index()
        )
        print("\n=== CPU slots / Mem-TB 分布（按内存档）===")
        print(summary)

        active_mem, active_cpu = 0.0, 0
        concurrency = []

        for ts in sorted(delta):
            for evt, mem, slots in delta[ts]:
                if evt == "start":
                    active_mem += mem
                    active_cpu += slots
                else:
                    active_mem -= mem
                    active_cpu -= slots
            concurrency.append((ts, active_mem, active_cpu))

        df_concurrent = pd.DataFrame(concurrency, columns=["timestamp", "mem_tb", "cpu_slots"])
        df_concurrent.to_csv("concurrent_usage.csv", index=False)
        print("\n并发时间序列已保存：concurrent_usage.csv")

        hours_per_bucket = window_sec / 3600
        global_cpu_hr = (df_concurrent["cpu_slots"] * hours_per_bucket).sum()
        global_mem_hr = (df_concurrent["mem_tb"]  * hours_per_bucket).sum()
        global_ratio  = global_cpu_hr / global_mem_hr      # slots / TB
        print(f"\n[指标1] 全局平均 CPU slots / TB = {global_ratio:,.2f}")

        peak_mem = df_concurrent["mem_tb"].max()
        peak_cpu = df_concurrent["cpu_slots"].max()
        print(f"[指标2] 峰值并发  Mem={peak_mem:,.2f} TB  CPU={peak_cpu:,d} slots")

        p95_per_bin = (df.groupby("mem_bin")
                         ["slots_per_tb"]
                         .quantile(0.95)
                         .rename("p95_slots_per_tb"))
        print("\n[指标3] 各档 P95 slots / TB")
        print(p95_per_bin)

        cv_per_bin = (df.groupby("mem_bin")
                        ["slots_per_tb"]
                        .agg(lambda x: x.std() / x.mean())
                        .rename("cv"))
        print("\n[指标4] 各档 CPU 密度波动 (CV)")
        print(cv_per_bin)

        rho = df[["mem_tb", "cpu_slots"]].corr("spearman").iloc[0, 1]
        print(f"\n[指标5] Spearman ρ(CPU,Mem) = {rho:.3f}")

        df["slots_per_tb"] = df["cpu_slots"] / df["mem_tb"]
        df["over_ratio"]  = df["slots_per_tb"] / global_ratio
        over_mean = df["over_ratio"].mean()
        over_p90  = df["over_ratio"].quantile(0.9)
        print(f"[指标6] Over-commit 比  Mean={over_mean:.2f}  P90={over_p90:.2f}")

        return summary, df_concurrent
    def ffd_pack(self, mem_list):
        caps = [int(4096 * 0.8), int(2048 * 0.8), int(500 * 0.9)]
        counts = [0, 0, 0]
        bins = []

        mem_list.sort(reverse=True)

        for mem in mem_list:
            placed = False
            for i in range(len(bins)):
                if bins[i] >= mem:
                    bins[i] -= mem
                    placed = True
                    break
            if not placed:
                for idx, cap in enumerate(caps[::-1]):
                    if cap >= mem:
                        counts[len(caps) - 1 - idx] += 1
                        bins.append(cap - mem)
                        break
        return counts[2], counts[1], counts[0]

    def get_total_duration_by_memory(self, start_date, end_date):
        s = Search(using=self.es_db.es_client, index='job_*')
        s = s.query("range", date={"gte": start_date, "lte": end_date, "format": "yyyy-MM-dd"})

        results = scan(self.es_db.es_client, query=s.to_dict(), size=1000)

        memory_bins = {}
        total_count = 0

        for hit in results:
            max_mem = hit['_source'].get('max_mem', 0)
            duration = hit['_source'].get('run_time', 0)

            bin_key = int(max_mem // (250 * 1024)) * 250

            memory_bins[bin_key] = memory_bins.get(bin_key, 0) + duration
            total_count += 1

        records = [{'memory_bin': k, 'total_duration': v} for k, v in sorted(memory_bins.items())]

        df = pd.DataFrame(records)

        return df, total_count


def plot_max_mem_histogram(df, y_label='Days'):
    memory_bins = df['memory_bin']
    total_durations_in_days = df['total_duration'] / (24 * 3600)

    plt.figure(figsize=(12, 6))

    bars = plt.bar(memory_bins, total_durations_in_days, width=160, edgecolor='black', color=plt.cm.tab20.colors)

    plt.xlabel('Memory Bin (GB)', fontsize=14)
    plt.ylabel(f'Total Duration ({y_label})', fontsize=14)
    plt.title('Total Job Duration per Memory Bin', fontsize=16)

    plt.xticks(memory_bins)

    plt.ticklabel_format(axis='y', style='plain')

    for bar in bars:
        height = bar.get_height()
        plt.text(bar.get_x() + bar.get_width() / 2, height, f'{height:.1f}', ha='center', va='bottom', fontsize=10)

    plt.grid(axis='y', linestyle='--', alpha=0.7)

    plt.tight_layout()
    plt.show()


def generate_memory_duration_table(df):
    """
    输入：
        df: 包含 memory_bin 和 total_duration（单位秒）的 DataFrame
    输出：
        含内存档位、总时长（天）、占比的 DataFrame
    """
    df['total_duration_days'] = df['total_duration'] / (24 * 3600)

    total_duration_all = df['total_duration_days'].sum()

    df['percentage'] = df['total_duration_days'] / total_duration_all * 100

    final_df = df[['memory_bin', 'total_duration_days', 'percentage']]
    final_df = final_df.rename(columns={
        'memory_bin': 'Memory Bin (GB)',
        'total_duration_days': 'Total Duration (Days)',
        'percentage': 'Percentage (%)'
    })
    final_df.to_excel('result.xlsx')

    return final_df


def main():
    data_processor = DataProcessor()
    data_processor.analyze_server_needs(start_date='2025-06-01', end_date='2025-07-02')
    # data_processor.cpu_slots_mem_ratio(start_date='2025-06-01', end_date='2025-06-30')
    # df.to_csv('job_data.csv')
    # print("The number of the job:", count)
    # plot_max_mem_histogram(df)
    # generate_memory_duration_table(df)


if __name__ == '__main__':
    main()


