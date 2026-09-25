'use strict';

const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('cfdDesktop', Object.freeze({
  isDesktop: true,
  platform: process.platform,
}));
