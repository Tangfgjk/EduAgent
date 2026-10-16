import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { Application } from '@wenjin/ui';
import '@wenjin/ui/styles.css';
import { AdminApp } from './admin';

createRoot(document.getElementById('root')!).render(<StrictMode><BrowserRouter basename="/admin"><Application role="admin">{account => <AdminApp key={account.id} account={account}/>}</Application></BrowserRouter></StrictMode>);
