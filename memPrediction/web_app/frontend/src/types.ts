export interface MemoryData {
  id: string;
  user: string;
  excess_mem_quantity: number;
  insufficient_mem_quantity: number;
  job_num: number;
  job_duration_average: number;
  max_mem_average: number;
  rusage_mem_average: number;
  max_mem_quantile_95: number;
  excess_cpu_quantity: number;
  timestamp: string;
}

export interface JobMemoryData {
  job_id: number;
  started_time: number;
  job_name: string;
  user: string;
  status: string;
  project: string;
  queue: string;
  cwd: string;
  command: string;
  rusage_mem: number;
  max_mem: number;
  avg_mem: number;
  finished_time: number;
  run_time: number;
  job_description: string;
  interactive_mode: boolean;
  cpu_time: number;
  span_hosts: number;
  processors_requested: number;
  cpu_utilization: number;
}