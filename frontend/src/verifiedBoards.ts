/**
 * Boards MicroPythonOS is known to run on, shown as a reference table.
 *
 * Reference only: the UI never asks the user to pick one, and an unlisted
 * board that probes successfully is just as valid.
 */

export const verifiedBoards = [
  ["Freenove", "ESP32-S3 Display", "ESP32-S3", "触摸屏", "入门交互"],
  ["Fri3d Camp", "2024 Badge", "ESP32-S3", "徽章屏幕", "活动徽章"],
  ["Fri3d Camp", "2026 Badge", "ESP32-S3", "徽章屏幕", "活动作品"],
  ["LilyGO", "T4 V1.3", "ESP32", "大屏", "信息面板"],
  ["LilyGO", "T-Display S3", "ESP32-S3", "彩色小屏", "便携工具"],
  ["LilyGO", "T-HMI", "ESP32-S3", "触摸屏", "人机界面"],
  ["LilyGO", "T-Watch S3 Plus", "ESP32-S3", "腕上触摸屏", "穿戴应用"],
  ["M5Stack", "Core2", "ESP32", "触摸屏", "新手创作"],
  ["M5Stack", "Fire", "ESP32", "彩色屏", "传感器项目"],
  ["Makerfabs", "MaTouch ESP32-S3 SPI IPS 2.8\" + OV3660", "ESP32-S3", "2.8\" 触摸屏", "视觉项目"],
  ["Hardkernel", "ODROID-GO", "ESP32", "游戏屏幕", "掌机应用"],
  ["SQUiXL", "SQUiXL", "ESP32-S3", "触摸屏", "桌面信息"],
  ["DFRobot", "UniHiker K10", "ESP32-S3", "彩色屏", "STEM 课堂"],
  ["unPhone", "unPhone 9", "ESP32-S3", "触摸屏", "移动创作"],
  ["Waveshare", "ESP32-S3-Touch-LCD-2", "ESP32-S3", "2\" 触摸屏", "新手与展示"],
] as const;
