const { app, BrowserWindow, ipcMain } = require("electron");
const path = require("path");
const { spawn } = require("child_process");

const isDev = !app.isPackaged;
const BACKEND_PORT = 8765;
let backendProcess = null;

function startBackend() {
  if (isDev) return; // Dev script starts backend separately

  const pythonPath = path.join(process.resourcesPath, "backend", ".venv", "bin", "python");
  const mainPath = path.join(process.resourcesPath, "backend", "main.py");
  backendProcess = spawn(pythonPath, [mainPath], {
    env: { ...process.env, PYTHONPATH: process.resourcesPath },
  });
  backendProcess.stderr.on("data", (d) => console.error("[backend]", d.toString()));
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    titleBarStyle: "hiddenInset",
    backgroundColor: "#0f0e10",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (isDev) {
    win.loadURL("http://127.0.0.1:5173");
    win.webContents.openDevTools({ mode: "detach" });
  } else {
    win.loadFile(path.join(__dirname, "..", "frontend", "dist", "index.html"));
  }
}

app.whenReady().then(() => {
  startBackend();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (backendProcess) backendProcess.kill();
  if (process.platform !== "darwin") app.quit();
});

ipcMain.handle("get-api-base", () =>
  isDev ? `http://127.0.0.1:${BACKEND_PORT}` : `http://127.0.0.1:${BACKEND_PORT}`
);
