const { Menu } = require("electron");

function createApplicationMenu(options = {}) {
  const fileMenu = {
    label: "File",
    submenu: [
      {
        label: "Show Main Window",
        accelerator: "CmdOrCtrl+Shift+W",
        click: () => options.showWindow?.()
      },
      {
        label: "Import Text File...",
        accelerator: "CmdOrCtrl+Shift+I",
        click: () => options.importTextFile?.()
      },
      {
        label: "Choose Export Path...",
        accelerator: "CmdOrCtrl+Shift+E",
        click: () => options.chooseExportPath?.()
      },
      { type: "separator" },
      {
        label: "Open Logs Directory",
        click: () => options.openLogsDirectory?.()
      },
      {
        label: "Open Data Directory",
        click: () => options.openDataDirectory?.()
      },
      {
        label: "Open Runtime Directory",
        click: () => options.openRuntimeDirectory?.()
      },
      { type: "separator" },
      {
        label: "Quit WenShape",
        accelerator: "CmdOrCtrl+Q",
        click: () => options.requestQuit?.({ source: "menu" })
      }
    ]
  };

  const editMenu = {
    label: "Edit",
    submenu: [
      { role: "undo" },
      { role: "redo" },
      { type: "separator" },
      { role: "cut" },
      { role: "copy" },
      { role: "paste" },
      { role: "selectAll" }
    ]
  };

  const viewMenu = {
    label: "View",
    submenu: [
      {
        label: "Reload Workspace",
        accelerator: "CmdOrCtrl+R",
        click: () => options.reloadWindow?.()
      },
      { role: "togglefullscreen" }
    ]
  };

  if (options.isDev) {
    viewMenu.submenu.push({
      label: "Toggle DevTools",
      accelerator: "CmdOrCtrl+Alt+I",
      click: () => options.toggleDevTools?.()
    });
  }

  const windowMenu = {
    label: "Window",
    submenu: [
      {
        label: "Minimize To Tray",
        accelerator: "CmdOrCtrl+M",
        click: () => options.hideWindow?.()
      },
      {
        label: "Restore Window",
        click: () => options.showWindow?.()
      },
      { role: "front" }
    ]
  };

  const helpMenu = {
    label: "Help",
    submenu: [
      {
        label: "Show Desktop Log File",
        click: () => options.openMainLog?.()
      },
      {
        label: "Show Runtime Directory",
        click: () => options.openRuntimeDirectory?.()
      }
    ]
  };

  const template = [];

  // macOS requires an app-name menu as the first item.
  if (process.platform === "darwin") {
    template.push({
      label: "WenShape",
      submenu: [
        { role: "about" },
        { type: "separator" },
        { role: "services" },
        { type: "separator" },
        { role: "hide" },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        {
          label: "Quit WenShape",
          accelerator: "CmdOrCtrl+Q",
          click: () => options.requestQuit?.({ source: "menu" })
        }
      ]
    });
  }

  template.push(
    fileMenu,
    editMenu,
    viewMenu,
    windowMenu,
    helpMenu
  );

  if (options.isDev) {
    template.push({
      label: "Development",
      submenu: [
        {
          label: "Reload Workspace",
          click: () => options.reloadWindow?.()
        },
        {
          label: "Toggle DevTools",
          click: () => options.toggleDevTools?.()
        }
      ]
    });
  }

  return Menu.buildFromTemplate(template);
}

module.exports = {
  createApplicationMenu
};
