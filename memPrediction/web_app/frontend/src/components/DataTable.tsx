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
import type { MemoryData } from '../types';

interface DataTableProps {
  data: MemoryData[];
  onUserClick?: (user: string) => void;
}

const ROWS_PER_PAGE = 10;

const DataTable: React.FC<DataTableProps> = ({ data, onUserClick }) => {
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
        const aValue = a[column as keyof MemoryData];
        const bValue = b[column as keyof MemoryData];

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
          <h3 className="text-lg font-semibold">内存使用数据</h3>
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
            className="flex items-center gap-1">
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
            <div className="min-w-[1400px]">
              <Table
                aria-label="Memory usage table"
                sortDescriptor={sortDescriptor}
                onSortChange={setSortDescriptor}>
                <TableHeader>
                  <TableColumn allowsSorting key="user">用户</TableColumn>
                  <TableColumn allowsSorting key="excess_mem_quantity">内存超用量(TB * H)</TableColumn>
                  <TableColumn allowsSorting key="insufficient_mem_quantity">内存不足量(TB * H)</TableColumn>
                  <TableColumn allowsSorting key="job_num">任务数量</TableColumn>
                  <TableColumn allowsSorting key="job_duration_average">平均任务时长(H)</TableColumn>
                  <TableColumn allowsSorting key="max_mem_average">平均最大内存(GB)</TableColumn>
                  <TableColumn allowsSorting key="rusage_mem_average">平均预留内存(GB)</TableColumn>
                  <TableColumn allowsSorting key="max_mem_quantile_95">95分位最大内存(GB)</TableColumn>
                  <TableColumn allowsSorting key="excess_cpu_quantity">CPU超用量</TableColumn>
                </TableHeader>
                <TableBody items={sortedItems}>
                  {(item) => (
                    <TableRow key={item.user}>
                      <TableCell>
                        <span
                          className="text-blue-600 cursor-pointer hover:underline whitespace-nowrap"
                          onClick={() => onUserClick?.(item.user)}
                        >
                          {item.user}
                        </span>
                      </TableCell>
                      <TableCell className="whitespace-nowrap">{item.excess_mem_quantity}</TableCell>
                      <TableCell className="whitespace-nowrap">{item.insufficient_mem_quantity}</TableCell>
                      <TableCell className="whitespace-nowrap">{item.job_num}</TableCell>
                      <TableCell className="whitespace-nowrap">{item.job_duration_average}</TableCell>
                      <TableCell className="whitespace-nowrap">{item.max_mem_average}</TableCell>
                      <TableCell className="whitespace-nowrap">{item.rusage_mem_average}</TableCell>
                      <TableCell className="whitespace-nowrap">{item.max_mem_quantile_95}</TableCell>
                      <TableCell className="whitespace-nowrap">{item.excess_cpu_quantity}</TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
              <div className="mt-4 text-sm text-gray-500 space-y-2">
                <p>* 内存超用量计算公式：∑ max(0, 预留内存 - 最大内存) * 任务时长</p>
                <p>* 内存不足量计算公式：∑ max(0, 最大内存 - 预留内存) * 任务时长</p>
                <p>* CPU 超用量计算公式：∑ max(0, CPU 利用率 - 1)</p>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default DataTable;