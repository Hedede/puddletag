#! /usr/bin/env python
# -*- coding: utf-8 -*-
#webdb.py

#Copyright (C) 2008-2009 concentricpuddle

#This file is part of puddletag, a semi-good music tag editor.

#This program is free software; you can redistribute it and/or modify
#it under the terms of the GNU General Public License as published by
#the Free Software Foundation; either version 2 of the License, or
#(at your option) any later version.

#This program is distributed in the hope that it will be useful,
#but WITHOUT ANY WARRANTY; without even the implied warranty of
#MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#GNU General Public License for more details.

#You should have received a copy of the GNU General Public License
#along with this program; if not, write to the Free Software
#Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
import sys, pdb
from puddleobjects import unique, OKCancel, PuddleThread, PuddleConfig, winsettings
from PyQt4.QtCore import *
from PyQt4.QtGui import *
from collections import defaultdict
import plugins
import puddlestuff.tagsources.musicbrainz as mbrainz
#import puddlestuff.tagsources.amazonsource as amazon
try:
    import puddlestuff.tagsources.amg as allmusic
except ImportError:
    allmusic = None
import puddlestuff.tagsources as tagsources
from puddlestuff.tagsources import RetrievalError, status_obj, write_log
from puddlestuff.constants import TEXT, COMBO, CHECKBOX, RIGHTDOCK
pyqtRemoveInputHook()
from findfunc import replacevars, getfunc
from functools import partial
from copy import copy
from puddlestuff.util import to_string

def display_tag(tag):
    """Used to display tags in in a human parseable format."""
    if not tag:
        return "<b>Error in pattern</b>"
    s = "<b>%s</b>: %s"
    tostr = lambda i: i if isinstance(i, basestring) else i[0]
    if ('__image' in tag) and tag['__image']:
        d = {'#images': unicode(len(tag['__image']))}
    else:
        d = {}
    return "<br />".join([s % (z, tostr(v)) for z, v in
                    sorted(tag.items() + d.items()) if z != '__image' and not
                    z.startswith('#')])

def display(pattern, tags):
    return replacevars(getfunc(pattern, tags), tags)

def strip(audio, taglist, reverse = False):
    if not taglist:
        return dict([(key, audio[key]) for key in audio if 
                        not key.startswith('#')])
    tags = taglist[::]
    if tags and tags[0].startswith('~'):
        reverse = True
        tags[0] = tags[0][1:]
    else:
        reverse = False
    if reverse:
        return dict([(key, audio[key]) for key in audio if key not in
                        tags and not key.startswith('#')])
    else:
        return dict([(key, audio[key]) for key in taglist if key in audio and
            not key.startswith('#')])

class TagListWidget(QWidget):
    def __init__(self, tags=None, parent=None):
        QWidget.__init__(self, parent)
        if not tags:
            tags = []
        label = QLabel()
        label.setText('&Tags')
        self._text = QLineEdit(u', '.join(tags))
        label.setBuddy(self._text)

        layout = QHBoxLayout()
        layout.setMargin(0)
        layout.addWidget(label, 0)
        layout.addWidget(self._text, 1)

        self.connect(self._text, SIGNAL('textChanged(QString)'), self.emitTags)

        self.setLayout(layout)

    def tags(self, text=None):
        if text is None:
            return [z.strip() for z in unicode(self._text.text()).split(u',')]
        else:
            return [z.strip() for z in unicode(text).split(u',')]

    def emitTags(self, text=None):
        self.emit(SIGNAL('tagschanged'), self.tags(text))

    def setTags(self, tags):
        self._text.setText(u', '.join(tags))
        
    def setToolTip(self, value):
        QWidget.setToolTip(self, value)
        self._text.setToolTip(value)

class SourcePrefs(QDialog):
    def __init__(self, title, controls, parent = None):
        QDialog.__init__(self, parent)
        vbox = QVBoxLayout()
        self._controls = []
        winsettings(title, self)
        self.setWindowTitle(u'Configure: ' + title)
        for desc, ctype, default in controls:
            if ctype == TEXT:
                control = QLineEdit(default)
                label = QLabel(desc)
                label.setBuddy(control)
                vbox.addWidget(label)
                vbox.addWidget(control)
            elif ctype == COMBO:
                control = QComboBox()
                control.addItems(default)
                label = QLabel(desc)
                label.setBuddy(control)
                vbox.addWidget(label)
                vbox.addWidget(control)
            elif ctype == CHECKBOX:
                control = QCheckBox(desc)
                if default:
                    control.setCheckState(Qt.Checked)
                else:
                    control.setCheckState(Qt.Unchecked)
                vbox.addWidget(control)
            self._controls.append(control)
        okcancel = OKCancel()
        self.connect(okcancel, SIGNAL('ok'), self.okClicked)
        self.connect(okcancel, SIGNAL('cancel'), self.close)
        vbox.addLayout(okcancel)
        vbox.addStretch()
        self.setLayout(vbox)

    def okClicked(self):
        values = []
        for control in self._controls:
            if isinstance(control, QLineEdit):
                values.append(unicode(control.text()))
            elif isinstance(control, QComboBox):
                values.append(control.currentIndex())
            elif isinstance(control, QCheckBox):
                values.append(bool(control.checkState()))
        self.emit(SIGNAL('tagsourceprefs'), values)
        self.close()


class SettingsDialog(QWidget):
    def __init__(self, parent = None, status = None):
        QWidget.__init__(self, parent)
        self.title = 'Tag Sources'
        cparser = PuddleConfig()
        text = cparser.get('tagsources', 'displayformat', '%track% - %title%')
        enablesort = cparser.get('tagsources', 'sort', True)
        sortorder = cparser.get('tagsources', 'sortorder', [u'artist', u'album'])
        albumformat = cparser.get('tagsources', 'albumformat', '%artist% - %album%')
        artoptions = cparser.get('tagsource', 'artoptions',
            ['Replace existing album art.', 'Append to existing album art.',
                "Leave artwork unchanged."])
        saveart = cparser.get('tagsources', 'saveart', False)
        coverdir = cparser.get('tagsources', 'coverdir', False)
        
        label = QLabel('&Display format for individual tracks.')
        self._text = QLineEdit(text)
        label.setBuddy(self._text)
        
        albumlabel = QLabel('Display format for &retrieved albums')
        self._albumdisp = QLineEdit(albumformat)
        albumlabel.setBuddy(self._albumdisp)

        self._enablesort = QCheckBox('&Sort Retrieved Albums')
        sortlabel = QLabel('Sort &order (comma separated &fields).')
        self._sortorder = QLineEdit()
        sortlabel.setBuddy(self._sortorder)
        self.connect(self._enablesort, SIGNAL('stateChanged(int)'),
            lambda state: self._sortorder.setEnabled(bool(state)))
        self._sortorder.setText(', '.join(sortorder))
        self._enablesort.setCheckState(Qt.Checked if enablesort 
            else Qt.Unchecked)

        self._savecover = QCheckBox('Save album art.')
        
        coverlabel = QLabel("&Directory to save retrieved album art "
            "(it will be created if it doesn't exist)")
        self._coverdir = QLineEdit(tagsources.COVERDIR)
        coverlabel.setBuddy(self._coverdir)
        
        self.connect(self._savecover, SIGNAL('stateChanged(int)'),
            lambda state: self._coverdir.setEnabled(bool(state)))
        
        vbox = QVBoxLayout()
        vbox.addWidget(label)
        vbox.addWidget(self._text)
        
        vbox.addWidget(albumlabel)
        vbox.addWidget(self._albumdisp)
        
        #vbox.addWidget(self._savecover)
        #vbox.addWidget(coverlabel)
        #vbox.addWidget(self._coverdir)

        vbox.addWidget(self._enablesort)
        vbox.addWidget(sortlabel)
        vbox.addWidget(self._sortorder)

        vbox.addStretch()
        self.setLayout(vbox)

    def applySettings(self, control):
        text = unicode(self._text.text())
        control.listbox.dispformat = text
        coverdir = unicode(self._coverdir.text())
        tagsources.set_coverdir(coverdir)

        sortorder = [z.strip() for z in 
                        unicode(self._sortorder.text()).split(',')]
        enablesort = bool(self._enablesort.checkState())

        if not enablesort:
            control.listbox.sortOrder = []
        else:
            control.listbox.sort(sortorder)
        
        albumdisp = unicode(self._albumdisp.text())
        control.listbox.albumformat = albumdisp
        
        savecover = bool(self._savecover.checkState())
        coverdir = unicode(self._coverdir.text())
        
        tagsources.set_coverdir(coverdir)
        tagsources.set_savecovers(savecover)

        cparser = PuddleConfig()
        cparser.set('tagsources', 'displayformat', text)
        cparser.set('tagsources', 'coverdir', coverdir)
        cparser.set('tagsources', 'sort', enablesort)
        cparser.set('tagsources', 'sortorder', sortorder)
        cparser.set('tagsources', 'albumformat', albumdisp)
        cparser.set('tagsources', 'savecover', savecover)
        cparser.set('tagsources', 'coverdir', coverdir)

class ChildItem(QTreeWidgetItem):
    def __init__(self, dispformat, track, albuminfo, *args):
        QTreeWidgetItem.__init__(self, *args)
        self.setFlags(Qt.ItemIsEnabled | Qt.ItemIsDragEnabled
                        | Qt.ItemIsSelectable)
        self.setToolTip(0, display_tag(track))
        self.displaytrack = track
        info = albuminfo.copy()
        info.update(track)
        track = info
        self.track = track
        self.dispformat = dispformat

    def _setPattern(self, val):
        self.setText(0, display(val, self.displaytrack))
        self._dispformat = val

    def _getPattern(self):
        return self._dispformat

    dispformat = property(_getPattern, _setPattern)

class ExactMatchItem(ChildItem):
    def __init__(self, dispformat, track, albuminfo, *args):
        ChildItem.__init__(self, dispformat, track, albuminfo, *args)
        self.setFlags(Qt.ItemIsEnabled | Qt.ItemIsDragEnabled
                      | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
        self.audio = track['#exact']
        self.setCheckState(0, Qt.Checked)

    def check(self):
        self.setCheckState(0, Qt.Checked)

    def unCheck(self):
        self.setCheckState(0, Qt.Unchecked)


def Item(dispformat, track, info):
    return ChildItem(dispformat, track, info) if '#exact' not in \
                track else ExactMatchItem(dispformat, track, info)

class ParentItem(QTreeWidgetItem):
    def __init__(self, albuminfo, dispformat, *itemargs):
        QTreeWidgetItem.__init__(self, *itemargs)
        self.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | 
                        Qt.ItemIsDropEnabled )
        self.tracks = None
        self._dispformat = '%artist% - %title%'
        self.setChildIndicatorPolicy(self.ShowIndicator)
        self.setIcon(0, QWidget().style().standardIcon(QStyle.SP_DirClosedIcon))
        self.setInfo(albuminfo, dispformat)
        self.hasTracks = True

    def addTracks(self, tracks, dispformat):
        self.takeChildren()
        self.setText(0, self.text(0) + ' [%d]' % len(tracks))
        def addChild(track):
            item = Item(dispformat, track, self.info)
            self.addChild(item)
            return item if '#exact' in track else None
        self.tracks = tracks
        return filter(None, [addChild(track) for track in tracks])

    def setInfo(self, info, dispformat=None):
        self.info = copy(info)
        artist = info['artist']
        album = info['album']
        self.artist = artist
        self.album = album
        self.setToolTip(0, display_tag(info))

        if dispformat is not None:
            self.dispformat = dispformat
        else:
            self.dispformat = self.dispformat
    
    def _setPattern(self, val):
        self.setText(0, display(val, self.info))
        self._dispformat = val

    def _getPattern(self):
        return self._dispformat

    dispformat = property(_getPattern, _setPattern)

class ReleaseWidget(QTreeWidget):
    def __init__(self, status, tagsource, parent = None):
        QTreeWidget.__init__(self, parent)
        self._dispformat = u'%track% - %title%'
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setSelectionMode(self.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(self.InternalMove)
        self._artists = {}
        self._albums = []
        self._tracks = {}
        self._dirtyrow = 0
        self._status = status
        self._tagsource = tagsource
        self.tagstowrite = []
        self.sortOrder = ['artist', 'album']
        self._albumformat = '%artist% - %album%'
        self.setSortOptions([['artist', 'album'], ['album', 'artist']])

        connect = lambda signal, slot: self.connect(self, SIGNAL(signal), slot)
        connect('itemSelectionChanged()', self._selChanged)
        connect('itemCollapsed (QTreeWidgetItem *)', self.setClosedIcon)
        connect('itemExpanded (QTreeWidgetItem *)', self.setOpenIcon)
        connect('itemChanged (QTreeWidgetItem *, int)', self._setExactMatches)
        connect('itemClicked (QTreeWidgetItem *, int)', self._selChanged)
    
    #def contextMenuEvent(self, event):
        #self._menu.exec_(event.globalPos())
        #event.accept()

    def setClosedIcon(self, item):
        item.setIcon(0, self.style().standardIcon(QStyle.SP_DirClosedIcon))

    def setOpenIcon(self, item):
        item.setIcon(0, self.style().standardIcon(QStyle.SP_DirOpenIcon))
        try:
            if item.tracks is None:
                self.gettracks([item], lambda items: self.updateStatus(items,
                    False))
        except AttributeError:
            return

    def _selChanged(self):
        rowindex = self.indexOfTopLevelItem
        toplevels = [z for z in self.selectedItems() if not z.parent()]
        if toplevels:
            for parent in toplevels:
                child = parent.child
                [child(row).setSelected(False) for row in
                    range(parent.childCount())]
            toretrieve = [item for item in toplevels if item.tracks is None
                and item.hasTracks]
            if toretrieve:
                self.gettracks(toplevels)
                return
        self._selectedTracks()

    def _setExactMatches(self, item, row=None):
        if hasattr(item, 'audio'):
            if item.checkState(0) != Qt.Unchecked:
                self.emit(SIGNAL('preview'), {item.audio:
                        strip(item.track, self.tagstowrite)})
            else:
                self.emit(SIGNAL('preview'), {item.audio: {}})

    def _children(self, item):
        child = item.child
        return [child(row) for row in xrange(item.childCount())]

    def _selectedTracks(self):
        rowindex = self.indexOfTopLevelItem
        selected = self.selectedItems()

        tags = self.tagstowrite[::]

        toplevels = [z for z in selected if not z.parent()]
        if len(toplevels) == 1 and (not toplevels[0].hasTracks):
            copytag =  toplevels[0].info.copy
            tracks = [strip(copytag(), tags) for z in 
                self._status['selectedrows']]
            self.emit(SIGNAL('preview'), tracks)
            return

        for item in toplevels:
            if '#extrainfo' in item.info:
                self.emit(SIGNAL('infoChanged'), u'<a href="%s">%s</a>' % (
                    item.info['#extrainfo'][1], item.info['#extrainfo'][0]))
        if toplevels:
            children = [z for z in selected if z.parent() and z.parent() not in toplevels]
            [children.extend(self._children(parent)) for parent in toplevels]
        else:
            children = selected
                
        tracks = [strip(child.track, tags) for child in children]
        if tracks:
            for tag in tracks:
                if '#extrainfo' in tag:
                    self.emit(SIGNAL('infoChanged'), u'<a href="%s">%s</a>' % (
                        tag['#extrainfo'][1], tag['#extrainfo'][0]))
                    break

            self.emit(SIGNAL('preview'),
                tracks[:len(self._status['selectedrows'])])

    def gettracks(self, items, fin = None):
        try:
            while self.t.isRunning():
                pass
        except AttributeError:
            pass
        self.setEnabled(False)
        self.emit(SIGNAL("statusChanged"), "Retrieving album tracks...")
        QApplication.processEvents()
        def func():
            ret = {}
            for item in items:
                if item.hasTracks and (item.tracks is not None):
                    continue
                self.emit(SIGNAL("statusChanged"),
                            u'Retrieving: <b>%s</b>' % item.text(0))
                try:
                    ret[item] = self._tagsource.retrieve(item.info)
                except RetrievalError, e:
                    self.emit(SIGNAL("statusChanged"), 
                        u'An error occured: ' + unicode(e))
            return ret
        self.t = PuddleThread(func)
        if fin is None:
            self.connect(self.t, SIGNAL("threadfinished"), self.updateStatus)
        else:
            self.connect(self.t, SIGNAL("threadfinished"), fin)
        self.t.start()

    def setReleases(self, releases):
        self.clear()
        self.emit(SIGNAL('infoChanged'), '')
        def item(text):
            i = QTreeWidgetItem([text])
            i.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            i.setIcon(0, self.style().standardIcon(QStyle.SP_DirClosedIcon))
            return i

        sortfunc = lambda element: u''.join(
                        [to_string(element[0].get(key)).lower() for key in 
                            self.sortOrder])
        for albuminfo, tracks in sorted(releases, key=sortfunc):
            artist = albuminfo['artist']
            album = albuminfo['album']
            parent = ParentItem(albuminfo, self.albumformat)
            self.addTopLevelItem(parent)
            if tracks:
                exact = parent.addTracks(tracks, self.dispformat)
                if exact:
                    [self._setExactMatches(item) for item in exact]
                    self.emit(SIGNAL('exactMatches'), True)
            elif tracks is None:
                parent.hasTracks = False

    def updateStatus(self, val, updateselection=True):
        self.setEnabled(True)
        if not val:
            return
        for item, (info, tracks) in val.items():
            if item.tracks is not None:
                continue
            item.setInfo(info)
            if tracks is None:
                item.hasTracks = False
            else:
                item.addTracks(tracks, self.dispformat)
        self.emit(SIGNAL("statusChanged"), "Track retrieval successful.")
        if updateselection:
            self._selectedTracks()

    def _getDispFormat(self):
        return self._dispformat

    def _setDispFormat(self, val):
        self._dispformat = val
        iterator = QTreeWidgetItemIterator(self,
                        QTreeWidgetItemIterator.NoChildren)
        while iterator.value():
            item = iterator.value()
            item.dispformat = val
            iterator += 1

    dispformat = property(_getDispFormat, _setDispFormat)
    
    def _getAlbumFormat(self):
        return self._albumformat

    def _setAlbumFormat(self, val):
        take = self.topLevelItem
        for item in [take(row) for row in range(self.topLevelItemCount())]:
            if item:
                item.dispformat = val
        self._albumformat = val

    albumformat = property(_getAlbumFormat, _setAlbumFormat)

    def sort(self, order=None):
        if (not self.sortOrder) or (not self.topLevelItemCount()):
            return
        reverse = (order == self.sortOrder)
        if order:
            self.sortOrder = order
        take = self.takeTopLevelItem
        items = [take(0) for row in range(self.topLevelItemCount())]
        self.clear()
        sortfunc = lambda item: u''.join(
                        [to_string(item.info.get(key)).lower() for key in 
                            self.sortOrder]).lower()
        values = [sortfunc(item) for item in items]
        for item in sorted(items, key=sortfunc, reverse=reverse):
            if item:
                self.addTopLevelItem(item)
    
    def setSortOptions(self, options):
        self.sortOptions = options
        menu = QMenu(self)
        menu.addAction('Sort By')
        menu.addSeparator()
        for option in self.sortOptions:
            action = QAction(u'/'.join(option), menu)
            self.connect(action, SIGNAL('triggered()'), 
                lambda: self.sort(option))
            menu.addAction(action)
        self._menu = menu

class MainWin(QWidget):
    def __init__(self, status, parent = None):
        QWidget.__init__(self, parent)
        self.settingsdialog = SettingsDialog
        self.emits = ['writepreview', 'setpreview', 'clearpreview',
                      'logappend']
        self.receives = []
        self.setWindowTitle("Tag Sources")
        self._status = status
        if allmusic:
            tagsources = [mbrainz, allmusic]
        else:
            tagsources = [mbrainz]
        tagsources.extend(plugins.tagsources)
        self._tagsources = [module.info[0]() for module in tagsources]
        self._configs = [module.info[1] for module in tagsources]
        self._tagsource = self._tagsources[0]
        self._tagstowrite = [[] for z in self._tagsources]
        self._sourcenames = [z.name for z in tagsources]
        self._lastindex = 0

        self.sourcelist = QComboBox()
        self.sourcelist.addItems(self._sourcenames)
        self.connect(self.sourcelist, SIGNAL('currentIndexChanged (int)'),
                        self._changeSource)
        sourcelabel = QLabel('Sour&ce: ')
        sourcelabel.setBuddy(self.sourcelist)

        preferences = QToolButton()
        preferences.setIcon(QIcon(':/preferences.png'))
        preferences.setToolTip('Configure')
        self.connect(preferences, SIGNAL('clicked()'), self.configure)

        sourcebox = QHBoxLayout()
        sourcebox.addWidget(sourcelabel)
        sourcebox.addWidget(self.sourcelist, 1)
        sourcebox.addWidget(preferences)
        self._prefbutton = preferences

        self._searchparams = QLineEdit()
        tooltip = "Enter search parameters here. If empty, the selected files are used. <ul><li><b>artist;album</b> searches for a specific album/artist combination.</li> <li>For multiple artist/album combinations separate them with the '|' character. eg. <b>Amy Winehouse;Back To Black|Outkast;Atliens</b>.</li> <li>To list the albums by an artist leave off the album part, but keep the semicolon (eg. <b>Ratatat;</b>). For a album only leave the artist part as in <b>;Resurrection.</li></ul>"
        self._searchparams.setToolTip(tooltip)

        self.getinfo = QPushButton("&Search")
        self.getinfo.setDefault(True)
        self.getinfo.setAutoDefault(True)
        self.connect(self._searchparams, SIGNAL('returnPressed()'), self.getInfo)
        self.connect(self.getinfo , SIGNAL("clicked()"), self.getInfo)

        self._writebutton = QPushButton('&Write')
        self._writebutton.setEnabled(False)
        clear = QPushButton("Clea&r preview")

        self.connect(self._writebutton, SIGNAL("clicked()"), self._write)
        self.connect(clear, SIGNAL("clicked()"), self._clear)

        self.label = QLabel("Select files and click on Search to retrieve "
                            "metadata.")

        self.listbox = ReleaseWidget(status, self._tagsource)

        self._taglist = TagListWidget()
        tooltip = 'Enter a comma seperated list of fields to write. <br /><br />Eg. <b>artist, album, title</b> will only write the artist, album and title fields of the retrieved tags. <br /><br />If you want to exclude some fields, but write all others start the list the tilde (~) character. Eg <b>~composer, __image</b> will write all fields but the composer and __image fields.'
        self._taglist.setToolTip(tooltip)
        self.connect(self._taglist, SIGNAL('tagschanged'), self._changeTags)
        self.connect(self.listbox, SIGNAL('statusChanged'), self.label.setText)
        self.connect(status_obj, SIGNAL('statusChanged'), self.label.setText)
        self.connect(self.listbox, SIGNAL('itemSelectionChanged()'),
                        self._enableWrite)
        self.connect(self.listbox, SIGNAL('exactMatches'),
                        self._enableWrite)
        self.connect(self.listbox, SIGNAL('preview'),
                        lambda tags: self.emit(SIGNAL('setpreview'), tags))
        self.connect(status_obj, SIGNAL('logappend'),
                        lambda text: self.emit(SIGNAL('logappend'), text))
        
        infolabel = QLabel()
        self.connect(self.listbox, SIGNAL('infoChanged'), infolabel.setText)
        
        hbox = QHBoxLayout()
        hbox.addWidget(self._searchparams, 1)
        hbox.addWidget(self.getinfo, 0)

        vbox = QVBoxLayout()
        vbox.addLayout(sourcebox)
        vbox.addLayout(hbox)
        vbox.addWidget(self.label)
        vbox.addWidget(self.listbox, 1)
        hbox = QHBoxLayout()
        hbox.addWidget(infolabel, 1)
        hbox.addStretch()
        hbox.addWidget(self._writebutton)
        hbox.addWidget(clear)
        vbox.addLayout(hbox)

        vbox.addWidget(self._taglist)

        self.setLayout(vbox)
        self._changeSource(0)

    def _clear(self):
        self.emit(SIGNAL('clearpreview'))

    def _changeSource(self, index):
        self._tagsource = self._tagsources[index]
        self.listbox._tagsource = self._tagsource
        if hasattr(self._tagsource, 'preferences'):
            self._config = self._tagsource.preferences
        else:
            self._config = self._configs[index]
        if not self._config:
            self._prefbutton.hide()
        else:
            self._prefbutton.show()
        self._lastindex = index
        self._taglist.setTags(self._tagstowrite[index])

    def _changeTags(self, tags):
        self.listbox.tagstowrite = tags
        self.listbox._selectedTracks()
        self._tagstowrite[self._lastindex] = tags

    def _enableWrite(self, value = None):
        if value is None:
            value = self.listbox.selectedItems()
        if value:
            self._writebutton.setEnabled(True)
        else:
            self._writebutton.setEnabled(False)

    def _write(self):
        self.emit(SIGNAL('writepreview'))
        self.label.setText("<b>Tags were written.</b>")

    def closeEvent(self, e):
        self._clear()

    def getInfo(self):
        tags = self._status['selectedfiles']
        self.label.setText('Retrieving album info.')
        if self._searchparams.text():
            text = unicode(self._searchparams.text())
            params = defaultdict(lambda:[])
            try:
                text = [z.split(';') for z in text.split(u'|') if z]
                [params[z.strip()].append(v.strip()) for z, v in text]
            except ValueError:
                self.label.setText('<b>Error parsing artist/album combinations.</b>')
                self.getinfo.setEnabled(True)
                return
        else:
            if not tags:
                self.label.setText('<b>Select some files or enter search paramaters.</b>')
                return
            params = None

        def retrieve():
            try:
                if params:
                    return self._tagsource.search(params=params)
                else:
                    return self._tagsource.search(audios=tags)
            except RetrievalError, e:
                return 'An error occured: %s' % unicode(e)
        self.getinfo.setEnabled(False)
        self._t = PuddleThread(retrieve)
        self.connect(self._t, SIGNAL('threadfinished'), self.setInfo)
        self._writebutton.setEnabled(False)
        self._t.start()

    def configure(self):
        config = self._config
        if config is None:
            return
        if hasattr(config, 'connect'):
            win = config(parent=self)
        else:
            win = SourcePrefs(self._tagsource.name, config, self)
        win.setModal(True)
        self.connect(win, SIGNAL('tagsourceprefs'), self._tagsource.applyPrefs)
        win.show()

    def setInfo(self, retval):
        self.getinfo.setEnabled(True)
        if isinstance(retval, basestring):
            self.label.setText(retval)
        else:
            self.listbox.setReleases(retval)
            self.label.setText(u'Searching complete.')

    def loadSettings(self):
        settings = PuddleConfig()
        source = settings.get('tagsources', 'lastsource', 'Musicbrainz')
        self._tagstowrite = [settings.get('tagsourcetags', name , []) for
                                name in self._sourcenames]
        index = self.sourcelist.findText(source)
        self.sourcelist.setCurrentIndex(index)
        self._taglist.setTags(self._tagstowrite[index])
        df = settings.get('tagsources', 'displayformat', u'%track% - %title%')
        self.listbox.dispformat = df
        enablesort = settings.get('tagsources', 'sort', True)
        sortorder = settings.get('tagsources', 'sortorder', [u'artist', u'album'])
        if enablesort:
            self.listbox.sortOrder = sortorder
        else:
            self.listbox.sortOrder = []
        albumformat = settings.get('tagsources', 'albumformat', 
                                        '%artist% - %album%')
        self.listbox.albumformat = albumformat

    def saveSettings(self):
        settings = PuddleConfig()
        settings.set('tagsources', 'lastsource', self.sourcelist.currentText())
        for i, name in enumerate(self._sourcenames):
            settings.set('tagsourcetags', name , self._tagstowrite[i])

control = ('Tag Sources', MainWin, RIGHTDOCK, False)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    status = {}
    status['selectedfiles'] = exampletags.tags
    win = MainWin(status)
    win.show()
    app.exec_()