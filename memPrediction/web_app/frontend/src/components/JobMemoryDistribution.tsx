import React, { useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { Button } from "@nextui-org/react";
import { FaChevronDown, FaChevronUp } from "react-icons/fa";
import type { JobMemoryData } from '../types';

interface JobMemoryDistributionProps {
  data: JobMemoryData[];
}

const JobMemoryDistribution: React.FC<JobMemoryDistributionProps> = ({ data }) => {
  const [isExpanded, setIsExpanded] = useState(true);

  const memoryRanges = [0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 4096];
  const distribution = memoryRanges.slice(0, -1).map((min, index) => {
  const max = memoryRanges[index + 1];
  const count = data.filter(job => {
    const jobMemGB = job.max_mem / 1024;
    return jobMemGB >= min && jobMemGB < max;
  }).length;
  return {
    range: `${min}-${max}G`,
    count,
    users: data.filter(job => {
      const jobMemGB = job.max_mem / 1024;
      return jobMemGB >= min && jobMemGB < max;
    }).reduce((acc, job) => {
      acc[job.user] = (acc[job.user] || 0) + 1;
      return acc;
    }, {} as Record<string, number>)
  };
});

  const lastMin = memoryRanges[memoryRanges.length - 1];
  const lastCount = data.filter(job => {
    const jobMemGB = job.max_mem / 1024;
    return jobMemGB >= lastMin;
  }).length;

  distribution.push({
    range: `${lastMin}G+`,
    count: lastCount,
    users: data.filter(job => {
      const jobMemGB = job.max_mem / 1024;
      return jobMemGB >= lastMin;
    }).reduce((acc, job) => {
      acc[job.user] = (acc[job.user] || 0) + 1;
      return acc;
    }, {} as Record<string, number>)
  });

  return (
    <div className="bg-white rounded-lg shadow">
      <div className="p-4 flex justify-between items-center">
        <h3 className="text-lg font-semibold">任务内存分布</h3>
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
      {isExpanded && (
        <div className="px-4 pb-4">
          <div className="overflow-x-auto">
            <div className="min-w-[2000px] h-[400px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={distribution}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis
                    dataKey="range"
                    label={{
                      value: '内存区间 (GB)',
                      position: 'insideBottom',
                      offset: -5
                    }}
                  />
                  <YAxis
                    label={{
                      value: '任务数量',
                      angle: -90,
                      position: 'insideLeft',
                      offset: 10
                    }}
                  />
                  <Tooltip
                    content={({ active, payload }) => {
                      if (active && payload && payload.length) {
                        const data = payload[0].payload;
                        return (
                          <div className="bg-white p-4 rounded-lg shadow border">
                            <p className="font-semibold">{`内存区间: ${data.range}`}</p>
                            <p>{`总任务数: ${data.count}`}</p>
                            <div className="mt-2">
                              <p className="font-semibold">用户分布:</p>
                              {Object.entries(data.users).map(([user, count]) => (
                                <p key={user}>{`${user}: ${count}个任务`}</p>
                              ))}
                            </div>
                          </div>
                        );
                      }
                      return null;
                    }}
                  />
                  <Legend />
                  <Bar
                    dataKey="count"
                    fill="#8884d8"
                    name="任务数量"
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default JobMemoryDistribution;
