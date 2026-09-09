CREATE DATABASE IF NOT EXISTS instagram_tracker CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE instagram_tracker;

CREATE TABLE IF NOT EXISTS snapshots (
    id INT AUTO_INCREMENT PRIMARY KEY,
    cuenta VARCHAR(255) NOT NULL,
    fecha DATE NOT NULL,
    total_followers INT NOT NULL,
    ganados INT DEFAULT 0,
    perdidos INT DEFAULT 0,
    creado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_cuenta_fecha (cuenta, fecha)
);

CREATE TABLE IF NOT EXISTS followers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    cuenta VARCHAR(255) NOT NULL,
    username VARCHAR(255) NOT NULL,
    primera_vez_visto DATE NOT NULL,
    ultima_vez_visto DATE NOT NULL,
    activo TINYINT(1) DEFAULT 1,
    UNIQUE KEY unique_cuenta_username (cuenta, username)
);

CREATE TABLE IF NOT EXISTS cambios (
    id INT AUTO_INCREMENT PRIMARY KEY,
    cuenta VARCHAR(255) NOT NULL,
    fecha DATE NOT NULL,
    username VARCHAR(255) NOT NULL,
    tipo ENUM('ganado', 'perdido') NOT NULL,
    creado_en DATETIME DEFAULT CURRENT_TIMESTAMP
);
