CREATE DATABASE IF NOT EXISTS ai_chat
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'aichat'@'localhost' IDENTIFIED BY 'change_me';
GRANT ALL PRIVILEGES ON ai_chat.* TO 'aichat'@'localhost';
FLUSH PRIVILEGES;
