import React, { useState, useEffect } from 'react';
import FilterPanel from '../components/FilterPanel';
import DataTable from '../components/DataTable';
import UserMemoryPieChart from '../components/UserMemoryPieChart';
import type { MemoryData } from '../types';
import appConfig from '../conf';

interface UserMemoryTabProps {
  onUserClick: (user: string) => void;
}

const UserMemoryTab: React.FC<UserMemoryTabProps> = ({ onUserClick }) => {
  const threeMonthsAgo = new Date();
  threeMonthsAgo.setMonth(threeMonthsAgo.getMonth() - 3);

  const [startDate, setStartDate] = useState<Date | null>(threeMonthsAgo);
  const [endDate, setEndDate] = useState<Date | null>(new Date());
  const [user, setUser] = useState('');
  const [allData, setAllData] = useState<MemoryData[]>([]);
  const [filteredData, setFilteredData] = useState<MemoryData[]>([]);

  const formatDate = (date) => {
    if (!date) return '';
    const d = new Date(date);
    const month = `${d.getMonth() + 1}`.padStart(2, '0');
    const day = `${d.getDate()}`.padStart(2, '0');
    return `${d.getFullYear()}-${month}-${day}`;
  };


  useEffect(() => {
    const formattedStartDate = formatDate(startDate);
    const formattedEndDate = formatDate(endDate);
    const apiUrl = `${appConfig.apiUrl}/summary`;
    console.log(apiUrl)

    const postData = {
    user: user,
    start_date: formattedStartDate,
    end_date: formattedEndDate
    };

    fetch(apiUrl, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(postData)
    })
    .then(response => response.json())
    .then(data => {
        console.debug('Received data:', data);
        setAllData(data)
        setFilteredData(data);
    })
    .catch(error => console.error('Error fetching data: ', error));
    }, [startDate, endDate]);

    const handleUserChange = (value: string) => {
        setUser(value);
        if (value.trim() === '') {
            setFilteredData(allData);
        } else {
            const filtered = allData.filter(item => item.user === value);
            setFilteredData(filtered);
        }
    };


  return (
    <div className="mt-6 space-y-6">
      <FilterPanel
        startDate={startDate}
        endDate={endDate}
        user={user}
        onStartDateChange={setStartDate}
        onEndDateChange={setEndDate}
        onUserChange={handleUserChange}
      />
      <div className="space-y-6">
        <DataTable 
          data={filteredData} 
          onUserClick={onUserClick}
        />
        <UserMemoryPieChart data={allData} />
      </div>
    </div>
  );
};

export default UserMemoryTab;