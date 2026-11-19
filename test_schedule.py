import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import app
import inbox
import schedule


class UpdateSchedule(unittest.TestCase):
    def test_calendar_and_due_agree(self):
        now = datetime(2026,11,22,9,30)  # Sunday
        weekly = dict(schedule.DEFAULT,frequency='weekly',hour=9,minute=31,weekday=7)
        self.assertEqual(schedule.calendar(weekly), {'Hour':9,'Minute':31,'Weekday':0})
        self.assertEqual(schedule.latest_slot(now,weekly), datetime(2026,11,15,9,31))
        values = {'schedule':json.dumps(weekly),'schedule_since':'2026-11-19T10:00:00','error':'prior failure'}
        self.assertFalse(schedule.due(now,values))
        values['error'] = ''
        self.assertTrue(schedule.due(now.replace(minute=31),values))
        hourly = dict(weekly,frequency='hourly')
        self.assertEqual(schedule.calendar(hourly), {'Minute':31})
        self.assertEqual(schedule.latest_slot(now,hourly), datetime(2026,11,22,8,31))
        self.assertFalse(schedule.due(now,{'day':'2026-11-22'}))
        self.assertTrue(schedule.due(now,{'day':'2026-11-21'}))
        with self.assertRaises(ValueError):
            schedule.validate(dict(weekly,minute=60))

    def test_failed_registration_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(schedule,'DATA',Path(tmp)), patch.object(inbox,'DB',Path(tmp)/'inbox.sqlite'):
            weekly = dict(schedule.DEFAULT,frequency='weekly', weekday=4)
            with patch.object(schedule,'register',side_effect=[RuntimeError('launchctl failure'), None]) as register:
                with self.assertRaises(RuntimeError):
                    schedule.install(weekly)
                self.assertEqual(register.call_args_list[-1].args[0],schedule.DEFAULT)
                self.assertEqual(schedule.settings(app.prefs()),schedule.DEFAULT)
            with patch.object(schedule,'register') as register:
                schedule.install(weekly)
                self.assertEqual(schedule.settings(app.prefs()),weekly)
                schedule.install()
                self.assertEqual(register.call_args.args[0],weekly)
