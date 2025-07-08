import React from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts';
import type { MemoryData } from './DataTable';

interface MemoryChartProps {
  data: MemoryData[];
}

const MemoryChart: React.FC<MemoryChartProps> = ({ data }) => {
  return (
    <div className="bg-white p-4 rounded-lg shadow">
      <h3 className="text-lg font-semibold mb-4">内存使用趋势</h3>
      <BarChart width={500} height={400} data={data}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="user" />
        <YAxis />
        <Tooltip />
        <Legend />
        <Bar dataKey="memory" fill="#8884d8" name="内存使用量 (MB)" />
      </BarChart>
    </div>
  );
};

export default MemoryChart;