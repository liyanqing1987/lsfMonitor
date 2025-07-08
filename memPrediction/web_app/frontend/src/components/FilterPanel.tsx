import React, { useState, KeyboardEvent } from 'react';
import DatePicker from 'react-datepicker';
import { Input } from '@nextui-org/react';
import "react-datepicker/dist/react-datepicker.css";

interface FilterPanelProps {
  startDate: Date | null;
  endDate: Date | null;
  user: string;
  jobId?: string;
  onStartDateChange: (date: Date | null) => void;
  onEndDateChange: (date: Date | null) => void;
  onUserChange: (value: string) => void;
  onJobIdChange?: (value: string) => void;
}

const FilterPanel: React.FC<FilterPanelProps> = ({
  startDate,
  endDate,
  user,
  jobId = '',
  onStartDateChange,
  onEndDateChange,
  onUserChange,
  onJobIdChange,
}) => {
  const [inputUser, setInputUser] = useState(user);
  const [inputJobId, setInputJobId] = useState(jobId);

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>, callback: (value: string) => void) => {
    if (e.key === 'Enter') {
      callback(e.currentTarget.value);
    }
  };

  return (
    <div className="flex gap-4 mb-6 items-center flex-wrap">
      <div className="flex items-center gap-2">
        <span className="text-sm">开始时间:</span>
        <DatePicker
          selected={startDate}
          onChange={onStartDateChange}
          className="px-3 py-2 border rounded-lg w-32"
          dateFormat="yyyy-MM-dd"
          placeholderText="选择开始日期"
          maxDate={endDate || new Date()}
          showTimeSelect={false}
          isClearable
        />
      </div>
      <div className="flex items-center gap-2">
        <span className="text-sm">结束时间:</span>
        <DatePicker
          selected={endDate}
          onChange={onEndDateChange}
          className="px-3 py-2 border rounded-lg w-32"
          dateFormat="yyyy-MM-dd"
          placeholderText="选择结束日期"
          minDate={startDate}
          maxDate={new Date()}
          showTimeSelect={false}
          isClearable
        />
      </div>
      <div className="flex items-center gap-2">
        <span className="text-sm">用户:</span>
        <Input
          value={inputUser}
          onChange={(e) => setInputUser(e.target.value)}
          onKeyDown={(e) => handleKeyDown(e, onUserChange)}
          placeholder="输入用户名并回车"
          className="w-40"
        />
      </div>
      {onJobIdChange && (
        <div className="flex items-center gap-2">
          <span className="text-sm">任务 ID:</span>
          <Input
            value={inputJobId}
            onChange={(e) => setInputJobId(e.target.value)}
            onKeyDown={(e) => handleKeyDown(e, onJobIdChange)}
            placeholder="输入任务 ID 并回车"
            className="w-40"
          />
        </div>
      )}
    </div>
  );
};

export default FilterPanel;