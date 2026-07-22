interface TelegramWebApp {
  initData?: string;
  initDataUnsafe?: {
    user?: {
      id?: number;
    };
  };
  platform?: string;
  ready?: () => void;
  close?: () => void;
  sendData?: (data: string) => void;
  openLink?: (url: string) => void;
}

interface Window {
  Telegram?: {
    WebApp?: TelegramWebApp;
  };
}
