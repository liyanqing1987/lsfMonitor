import React from 'react';
import Dashboard from './index';

const App: React.FC = () => {
    return (
        <div className="min-h-screen bg-gray-50 p-4">
            <div className="max-w-[1600px] mx-auto">
                <Dashboard />
            </div>
        </div>
    );
};

export default App;