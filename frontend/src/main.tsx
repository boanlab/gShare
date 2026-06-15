import React from 'react';
import ReactDOM from 'react-dom/client';
import App from '@/app/App';
import '@/styles/index.css';
import '@/i18n';
import { useUiStore } from '@/store/uiStore';

// Apply the stored theme before the first paint, so there is no light-to-dark flash.
useUiStore.getState().applyTheme();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
