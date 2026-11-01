"""One controller per Windows user; local IPC restores its existing window."""
import hashlib
import sys
from PySide6.QtCore import QLockFile, QStandardPaths
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from pathlib import Path


class SingleInstance:
    def __init__(self):
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)) / 'ZapretDesk'
        folder.mkdir(parents=True, exist_ok=True)
        self.name = 'zapret-gui-' + hashlib.sha256(str(folder).lower().encode()).hexdigest()[:24]
        self.lock = QLockFile(str(folder / 'gui.lock'))
        self.lock.setStaleLockTime(0)
        self.server = None

    def acquire(self):
        # An elevated replacement waits for the previous process to finish.
        if self.lock.tryLock(10000 if '--connect' in sys.argv else 0):
            return True
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if socket.waitForConnected(2000):
            socket.write(b'show')
            socket.waitForBytesWritten(1000)
            socket.disconnectFromServer()
        else:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(None, 'zapret GUI', 'Приложение уже запущено, но окно не ответило. Проверьте значок в трее.')
        return False

    def listen(self, restore):
        QLocalServer.removeServer(self.name)
        self.server = QLocalServer()
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        def activate():
            while self.server.hasPendingConnections():
                socket = self.server.nextPendingConnection()
                restore()
                socket.disconnectFromServer()
                socket.deleteLater()
        self.server.newConnection.connect(activate)
        if not self.server.listen(self.name):
            raise RuntimeError(self.server.errorString())
