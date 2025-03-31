import React, { useState, useMemo } from 'react';
import {
  Table,
  TableHeader,
  TableColumn,
  TableBody,
  TableRow,
  TableCell,
  Button,
  Pagination,
  SortDescriptor
} from "@nextui-org/react";
import { FaChevronDown, FaChevronUp } from "react-icons/fa";
import TruncatedCell from './TruncatedCell';
import type { JobMemoryData } from '../types';

interface JobMemoryTableProps {
  data: JobMemoryData[];
}

const ROWS_PER_PAGE = 10;

const JobMemoryTable: React.FC<JobMemoryTableProps> = ({ data }) => {
  const [isExpanded, setIsExpanded] = useState(true);
  const [page, setPage] = useState(1);
  const [sortDescriptor, setSortDescriptor] = useState<SortDescriptor>({
    column: undefined,
    direction: undefined,
  });

  const pages = Math.ceil(data.length / ROWS_PER_PAGE);

  const sortedItems = useMemo(() => {
    const { column, direction } = sortDescriptor;
    if (!column || !direction) {
      return data.slice((page - 1) * ROWS_PER_PAGE, page * ROWS_PER_PAGE);
    }

    return [...data]
      .sort((a, b) => {
        const aValue = a[column as keyof JobMemoryData];
        const bValue = b[column as keyof JobMemoryData];

        if (typeof aValue === 'number' && typeof bValue === 'number') {
          return direction === "ascending" ? aValue - bValue : bValue - aValue;
        }

        return direction === "ascending"
          ? String(aValue).localeCompare(String(bValue))
          : String(bValue).localeCompare(String(aValue));
      })
      .slice((page - 1) * ROWS_PER_PAGE, page * ROWS_PER_PAGE);
  }, [data, page, sortDescriptor]);

  return (
    <div className="bg-white rounded-lg shadow">
      <div className="p-4 flex justify-between items-center">
        <div className="flex items-center gap-4">
          <h3 className="text-lg font-semibold">任务内存使用数据</h3>
          <div className="text-sm text-gray-500">
            共 {data.length} 条数据
          </div>
        </div>
        <div className="flex items-center gap-4">
          <Pagination
            isCompact
            showControls
            showShadow
            color="primary"
            page={page}
            total={pages}
            onChange={(page) => setPage(page)}
          />
          <Button
            variant="light"
            size="sm"
            onClick={() => setIsExpanded(!isExpanded)}
            className="flex items-center gap-1"
          >
            {isExpanded ? (
              <>
                <FaChevronUp className="text-sm" />
                <span>收起</span>
              </>
            ) : (
              <>
                <FaChevronDown className="text-sm" />
                <span>展开</span>
              </>
            )}
          </Button>
        </div>
      </div>
      {isExpanded && (
        <div className="px-4 pb-4">
          <div className="overflow-x-auto">
            <Table
              aria-label="Job memory usage table"
              sortDescriptor={sortDescriptor}
              onSortChange={setSortDescriptor}
            >
              <TableHeader>
                <TableColumn allowsSorting key="job_id">任务ID</TableColumn>
                <TableColumn allowsSorting key="started_time">开始时间</TableColumn>
                <TableColumn allowsSorting key="job_name">任务名称</TableColumn>
                <TableColumn allowsSorting key="user">用户</TableColumn>
                <TableColumn allowsSorting key="status">状态</TableColumn>
                <TableColumn allowsSorting key="project">项目</TableColumn>
                <TableColumn allowsSorting key="queue">队列</TableColumn>
                <TableColumn allowsSorting key="cwd">工作目录</TableColumn>
                <TableColumn allowsSorting key="command">命令</TableColumn>
                <TableColumn allowsSorting key="rusage_mem">预留内存</TableColumn>
                <TableColumn allowsSorting key="max_mem">最大内存</TableColumn>
                <TableColumn allowsSorting key="avg_mem">平均内存</TableColumn>
                <TableColumn allowsSorting key="finished_time">结束时间</TableColumn>
                <TableColumn allowsSorting key="run_time">运行时间</TableColumn>
                <TableColumn allowsSorting key="job_description">任务描述</TableColumn>
                <TableColumn allowsSorting key="interactive_mode">交互模式</TableColumn>
                <TableColumn allowsSorting key="cpu_time">CPU时间</TableColumn>
                <TableColumn allowsSorting key="span_hosts">跨主机数</TableColumn>
                <TableColumn allowsSorting key="processors_requested">请求处理器</TableColumn>
                <TableColumn allowsSorting key="cpu_utilization">CPU利用率</TableColumn>
              </TableHeader>
              <TableBody items={sortedItems}>
                {(item) => (
                  <TableRow key={item.job_id}>
                    <TableCell className="whitespace-nowrap">{item.job_id}</TableCell>
                    <TableCell className="whitespace-nowrap">{new Date(item.started_time * 1000).toLocaleString()}</TableCell>
                    <TableCell className="whitespace-nowrap">{item.job_name}</TableCell>
                    <TableCell className="whitespace-nowrap">{item.user}</TableCell>
                    <TableCell className="whitespace-nowrap">{item.status}</TableCell>
                    <TableCell className="whitespace-nowrap">{item.project}</TableCell>
                    <TableCell className="whitespace-nowrap">{item.queue}</TableCell>
                    <TableCell><TruncatedCell content={item.cwd} /></TableCell>
                    <TableCell><TruncatedCell content={item.command} /></TableCell>
                    <TableCell className="whitespace-nowrap">{`${item.rusage_mem} MB`}</TableCell>
                    <TableCell className="whitespace-nowrap">{`${item.max_mem} MB`}</TableCell>
                    <TableCell className="whitespace-nowrap">{`${item.avg_mem} MB`}</TableCell>
                    <TableCell className="whitespace-nowrap">{new Date(item.finished_time * 1000).toLocaleString()}</TableCell>
                    <TableCell className="whitespace-nowrap">{`${item.run_time}s`}</TableCell>
                    <TableCell><TruncatedCell content={item.job_description} /></TableCell>
                    <TableCell className="whitespace-nowrap">{item.interactive_mode ? '是' : '否'}</TableCell>
                    <TableCell className="whitespace-nowrap">{`${item.cpu_time}s`}</TableCell>
                    <TableCell className="whitespace-nowrap">{item.span_hosts}</TableCell>
                    <TableCell className="whitespace-nowrap">{item.processors_requested}</TableCell>
                    <TableCell className="whitespace-nowrap">{`${(item.cpu_utilization * 100).toFixed(1)}%`}</TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
    </div>
  );
};

export default JobMemoryTable;