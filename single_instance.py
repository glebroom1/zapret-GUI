"""One controller per Windows user; local IPC restores its existing window."""
import hashlib
import sys
from PySide6.QtCore import QLockFile, QStandardPaths, QTimer
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
        if self.lock.tryLock(0):
            return True
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if socket.waitForConnected(2000):
            if '--tray' not in sys.argv:
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
                def read_command(peer=socket):
                    data = bytes(peer.readAll())
                    peer.setProperty('command', peer.property('command') + data)
                    command = peer.property('command')
                    if command == b'show':
                        restore()
                        peer.disconnectFromServer()
                    elif len(command) >= 4:
                        peer.disconnectFromServer()
                socket.setProperty('command', b'')
                socket.readyRead.connect(read_command)
                socket.disconnected.connect(socket.deleteLater)
                QTimer.singleShot(1500, socket, socket.disconnectFromServer)
                if socket.bytesAvailable():
                    read_command()
        self.server.newConnection.connect(activate)
        if not self.server.listen(self.name):
            raise RuntimeError(self.server.errorString())


def notify_existing():
    """Probe without keeping the lock, before requesting elevation."""
    instance = SingleInstance()
    if instance.lock.tryLock(0):
        instance.lock.unlock()
        return False
    socket = QLocalSocket()
    socket.connectToServer(instance.name)
    if not socket.waitForConnected(600):
        return False
    if '--tray' not in sys.argv:
        socket.write(b'show')
        socket.waitForBytesWritten(600)
    socket.disconnectFromServer()
    return True
