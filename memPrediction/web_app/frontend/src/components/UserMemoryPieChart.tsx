import React, { useState, useMemo } from 'react';
import { PieChart, Pie, Cell, Tooltip, Legend } from 'recharts';
import { Button } from "@nextui-org/react";
import { FaChevronDown, FaChevronUp } from "react-icons/fa";
import type { MemoryData } from '../types';

interface UserMemoryPieChartProps {
  data: MemoryData[];
}

const COLORS = ['#0088FE', '#00C49F', '#FFBB28', '#FF8042', '#8884d8', '#82ca9d', '#ffc658', '#ff7300', '#ff0000', '#00ff00', '#666666'];

const UserMemoryPieChart: React.FC<UserMemoryPieChartProps> = ({ data }) => {
  const [isExpanded, setIsExpanded] = useState(true);
  console.debug('Data type:', typeof data);
  console.debug('Data content:', data);

  const userMemoryMap = useMemo(() => {
    return data.reduce((acc, curr) => {
      acc[curr.user] = (acc[curr.user] || 0) + curr.excess_mem_quantity;
      return acc;
    }, {} as Record<string, number>);
  }, [data]);

  const pieData = useMemo(() => {
    const sortedUsers = Object.entries(userMemoryMap)
      .sort(([, a], [, b]) => b - a);
    
    const top10Users = sortedUsers.slice(0, 5);
    const otherUsers = sortedUsers.slice(5);
    
    const result = top10Users.map(([user, memory]) => ({
      name: user,
      value: memory
    }));

    if (otherUsers.length > 0) {
      const otherMemory = otherUsers.reduce((sum, [, memory]) => sum + memory, 0);
      result.push({
        name: '其他用户',
        value: otherMemory
      });
    }

    return result;
  }, [userMemoryMap]);

  return (
    <div className="bg-white rounded-lg shadow">
      <div className="p-4 flex justify-between items-center">
        <h3 className="text-lg font-semibold">用户内存使用占比（Top 5）</h3>
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
      {isExpanded && (
        <div className="px-4 pb-4">
          <div className="flex justify-center">
            <PieChart width={600} height={400}>
              <Pie
                data={pieData}
                cx={250}
                cy={180}
                innerRadius={60}
                outerRadius={120}
                fill="#8884d8"
                paddingAngle={5}
                dataKey="value"
                label={({ name, percent }) => `${name} (${(percent * 100).toFixed(0)}%)`}
              >
                {pieData.map((_, index) => (
                  <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip 
                formatter={(value: number) => `${value.toFixed(0)} TB*H`}
              />
              <Legend />
            </PieChart>
          </div>
        </div>
      )}
    </div>
  );
};

export default UserMemoryPieChart;