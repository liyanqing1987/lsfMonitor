import React, { useState } from 'react';
import { Tabs, Tab } from "@nextui-org/react";
import UserMemoryTab from './tabs/UserMemoryTab';
import JobMemoryTab from './tabs/JobMemoryTab';


const Dashboard: React.FC = () => {
  const [selectedTab, setSelectedTab] = useState("users");
  const [selectedUser, setSelectedUser] = useState("");
  const [startDate, setStartDate] = useState<Date | null>(new Date());
  const [endDate, setEndDate] = useState<Date | null>(new Date());
  const [user, setUser] = useState('');
  const [filteredUserData, setFilteredUserData] = useState<MemoryData[]>([]);
  const [filteredJobData, setFilteredJobData] = useState<JobMemoryData[]>([]);
  const [jobId, setJobId] = useState('');

  const handleUserClick = (user: string) => {
    setSelectedUser(user);
    setSelectedTab("jobs");
  };


  const handleJobIdChange = (value: string) => {
    setJobId(value);
    setSelectedTab("jobs");
  };

  return (
      <div>
        <Tabs
            selectedKey={selectedTab}
            onSelectionChange={(key) => setSelectedTab(key.toString())}
        >
          <Tab key="users" title="用户内存使用">
            <UserMemoryTab onUserClick={handleUserClick} />
          </Tab>
          <Tab key="jobs" title="任务内存分布">
            <JobMemoryTab selectedUser={selectedUser} />
          </Tab>
        </Tabs>
      </div>
  );
};


export default Dashboard;