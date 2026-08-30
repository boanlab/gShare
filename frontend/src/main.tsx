import React from 'react';
import ReactDOM from 'react-dom/client';
import App from '@/app/App';
// Pretendard covers Hangul and Latin in one variable face; the dynamic subset ships ~90 small
// woff2 chunks so a browser only fetches the ranges it actually renders.
import 'pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css';
// JetBrains Mono for figures, ids and costs; declared before the theme sheet that references it.
import '@/styles/fonts.css';
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
