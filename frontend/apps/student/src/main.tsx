import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { Application } from '@wenjin/ui';
import '@wenjin/ui/styles.css';
import { StudentApp } from './student';

createRoot(document.getElementById('root')!).render(<StrictMode><BrowserRouter basename="/student"><Application role="student">{account => <StudentApp key={account.id} account={account}/>}</Application></BrowserRouter></StrictMode>);
