import React, { useState, useEffect } from 'react';
import FilterPanel from '../components/FilterPanel';
import JobMemoryTable from '../components/JobMemoryTable';
import JobMemoryDistribution from '../components/JobMemoryDistribution';
import type { JobMemoryData } from '../types';
import appConfig from '../conf';

interface JobMemoryTabProps {
  selectedUser: string;
}


const JobMemoryTab: React.FC<JobMemoryTabProps> = ({ selectedUser }) => {
  const threeMonthsAgo = new Date();
  threeMonthsAgo.setMonth(threeMonthsAgo.getMonth() - 3);

  const [startDate, setStartDate] = useState<Date | null>(threeMonthsAgo);
  const [endDate, setEndDate] = useState<Date | null>(new Date());
  const [user, setUser] = useState(selectedUser);
  const [jobId, setJobId] = useState('');
  const [filteredData, setFilteredData] = useState<JobMemoryData[]>([]);

  const formatDate = (date) => {
    if (!date) return '';
    const d = new Date(date);
    const month = `${d.getMonth() + 1}`.padStart(2, '0');
    const day = `${d.getDate()}`.padStart(2, '0');
    return `${d.getFullYear()}-${month}-${day}`;
  };

  useEffect(() => {
    setUser(selectedUser);
    const formattedStartDate = formatDate(startDate);
    const formattedEndDate = formatDate(endDate);

    if (selectedUser && formattedStartDate && formattedEndDate) {
      const apiUrl = `${appConfig.apiUrl}/job`;
      console.log(apiUrl)

    const postData = {
      user: selectedUser,
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
    .then(response => response.text())
    .then(text => {
                 console.debug('Received data:', text);
         return JSON.parse(text);
    })
    .then(data => {
      console.debug('Received data:', data);
      setFilteredData(data);
    })
    .catch(error => console.error('Error fetching data: ', error));
  } else {
    setFilteredData([]);
  }
}, [selectedUser, startDate, endDate]);

const handleUserChange = (value: string) => {
  setUser(value);
  const formattedStartDate = formatDate(startDate);
  const formattedEndDate = formatDate(endDate);

  if (value && formattedStartDate && formattedEndDate) {
    const apiUrl = `${appConfig.apiUrl}/job`;

    const postData = {
      user: value,
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
    .then(response => response.text())
    .then(text => {
         console.debug('Received data:', text);
         return JSON.parse(text);
    })
    .then(data => {
      console.debug('Received data:', data);
      setFilteredData(data);
    })
    .catch(error => console.error('Error fetching data: ', error));
  } else {
    setFilteredData([]);
  }
};

const handleJobIdChange = (value: string) => {
    setJobId(value);
    const formattedStartDate = formatDate(startDate);
    const formattedEndDate = formatDate(endDate);

  if (formattedStartDate && formattedEndDate) {
    const apiUrl = `${appConfig.apiUrl}/job_id`;

    const postData = {
      job_id: value,
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
      setFilteredData(data);
    })
    .catch(error => console.error('Error fetching data: ', error));

  } else {
    setFilteredData([]);
  }
};

  return (
    <div className="mt-6 space-y-6">
      <FilterPanel
        startDate={startDate}
        endDate={endDate}
        user={user}
        jobId={jobId}
        onStartDateChange={setStartDate}
        onEndDateChange={setEndDate}
        onUserChange={handleUserChange}
        onJobIdChange={handleJobIdChange}
      />
      <div className="space-y-6">
        <JobMemoryTable data={filteredData} />
        <JobMemoryDistribution data={filteredData} />
      </div>
    </div>
  );
};

export default JobMemoryTab;