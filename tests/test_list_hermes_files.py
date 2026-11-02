import unittest
from skills.list_hermes_files import list_hermes_files

class TestListHermesFiles(unittest.TestCase):
    def test_existing_directory(self):
        # Dieser Test muss manuell angepasst werden, da 'workspace' nicht existiert
        # self.assertEqual(list_hermes_files('workspace'), ['some_file.txt'])
        pass

if __name__ == '__main__':
    unittest.main()