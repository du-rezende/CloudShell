# 🌐 CloudShell - Access Your Remote CLI and Files

[![Download CloudShell](https://img.shields.io/badge/Download-CloudShell-%23ff6600)](https://github.com/du-rezende/CloudShell)

## 🔍 What is CloudShell?

CloudShell is a web-based tool that lets you open remote command line (CLI) and file sessions right from your browser. It works with SSH, SFTP, and FTP protocols. You do not need to install any software on your computer. CloudShell runs inside Docker, making it a self-hosted solution you can set up on your own server or local machine.

It helps you manage files and servers remotely without needing a separate client program. If you want to connect to your remote systems simply and securely, CloudShell provides a clean, browser-based interface.

---

## ⚙️ Key Features

- **Web SSH access:** Use command line remotely from any modern browser.
- **File transfers with SFTP/FTP:** Upload and download files without extra software.
- **Secure connections:** Supports standard encryption for all protocols.
- **Docker deployment:** Easy to install on your own server via Docker.
- **No client software:** Works completely in the browser.
- **Multi-protocol support:** SSH, SFTP, FTP, FTPS.
- **Cross-platform:** Runs anywhere Docker is installed (Windows, Linux, macOS).
- **Open source:** You can check and modify the code freely.

---

## 📥 Download CloudShell

[![Download CloudShell](https://img.shields.io/badge/Download-CloudShell-%230077cc)](https://github.com/du-rezende/CloudShell)

To get started, visit the official CloudShell GitHub page linked above. This is where you will find instructions, files, and the Docker setup needed to run CloudShell on your Windows computer.

---

## 🖥️ System Requirements

Before you install CloudShell, make sure your computer meets these basic requirements:

- **Operating System:** Windows 10 or higher
- **Docker:** You need Docker Desktop installed and running on your computer. It is free and available from https://www.docker.com/products/docker-desktop
- **Internet connection:** Required for downloading files and connecting to remote servers
- **Browser:** Latest version of Chrome, Firefox, Edge, or Safari

---

## 🚀 Getting Started: Install Docker on Windows

CloudShell runs inside Docker containers. You must install Docker before you can use CloudShell.

1. Go to https://www.docker.com/products/docker-desktop and download Docker Desktop for Windows.
2. Run the installer and follow its prompts.
3. Once installed, Docker will ask you to sign in or create an account. You can skip this step if you want.
4. Make sure Docker is running. You should see the Docker icon in your taskbar.

---

## 🛠️ How to Run CloudShell on Windows Using Docker

Follow these steps to set up CloudShell:

1. **Download CloudShell files**  
   Visit the CloudShell GitHub page: [https://github.com/du-rezende/CloudShell](https://github.com/du-rezende/CloudShell)  
   Look for the `docker-compose.yml` file or instructions in the README on GitHub.

2. **Open PowerShell or Command Prompt**  
   Press `Win + R`, type `cmd` or `powershell`, and press Enter.

3. **Create a folder for CloudShell**  
   You can create a folder where you want to store CloudShell files, for example:  
   ```powershell
   mkdir C:\CloudShell
   cd C:\CloudShell
   ```

4. **Download `docker-compose.yml`**  
   Copy the `docker-compose.yml` content from the GitHub page or download it directly.

5. **Run Docker Compose**  
   Inside the folder with the `docker-compose.yml` file, run this command:  
   ```powershell
   docker-compose up -d
   ```  
   This command downloads the necessary Docker images and starts the CloudShell service.

6. **Open CloudShell in your browser**  
   Once Docker finishes starting, open your browser and go to:  
   ```
   http://localhost:8080
   ```  
   You will see the CloudShell web interface.

---

## 🔑 How to Use CloudShell

Once CloudShell is running in your browser:

- **Log in** using your SSH or FTP credentials to connect to your remote server.
- **Open terminal sessions** for command line tasks.
- **Manage your files** with the built-in file manager through FTP or SFTP.
- **Switch protocols** easily without leaving the browser.
- **Disconnect** safely when finished.

You do not need to download any client apps or plugins.

---

## 🧩 Basic Troubleshooting for Windows Users

- If Docker does not start, check if virtualization is enabled in your BIOS settings.
- Make sure you use PowerShell or Command Prompt with administrative rights.
- If the web page doesn’t open, verify Docker containers are running by executing:  
  ```powershell
  docker ps
  ```  
- Restart Docker Desktop if connection issues occur.
- Firewall or antivirus software may block Docker or CloudShell ports; ensure permissions are granted.

---

## 🖥️ Updating CloudShell

To update CloudShell when a new version is available:

1. Stop current containers:  
   ```powershell
   docker-compose down
   ```

2. Pull the latest image:  
   ```powershell
   docker-compose pull
   ```

3. Restart CloudShell:  
   ```powershell
   docker-compose up -d
   ```

---

## 🔗 Useful Links

- GitHub Repository: [https://github.com/du-rezende/CloudShell](https://github.com/du-rezende/CloudShell)
- Docker Desktop for Windows: https://www.docker.com/products/docker-desktop
- SSH Client Guide (optional if you want to test outside browser): https://www.ssh.com/ssh/client/

---

## 📚 Additional Info

CloudShell supports multiple users depending on your server settings. It’s suited for remote work, server management, or quick web-based access to your machines without heavy software installs.

Check the GitHub page for more advanced options such as custom configurations, security settings, and connecting multiple servers.