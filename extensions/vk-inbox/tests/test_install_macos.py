import importlib.util,json,pathlib,tempfile,unittest,zipfile
SCRIPT=pathlib.Path(__file__).parents[1]/'scripts/install_macos.py'
class InstallTests(unittest.TestCase):
 def module(self):
  s=importlib.util.spec_from_file_location('installer',SCRIPT);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
 def package(self,root,extra=None):
  p=root/'release.zip'
  with zipfile.ZipFile(p,'w') as z:
   z.writestr('manifest.json',json.dumps({'name':'Universal UserIO Agent','version':'0.9.11','manifest_version':3}))
   z.writestr('background.js','new');z.writestr('lib/config.js','public')
   if extra:z.writestr(*extra)
  return p
 def test_existing_configuration_and_backup_survive(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d);active=p/'active';(active/'lib').mkdir(parents=True)
   (active/'background.js').write_text('old');(active/'lib/config.js').write_text('private-config')
   result=self.module().install(self.package(p),active,p/'backups')
   self.assertEqual((active/'background.js').read_text(),'new');self.assertEqual((active/'lib/config.js').read_text(),'private-config')
   with zipfile.ZipFile(result['backup']) as z:self.assertEqual(z.read('background.js'),b'old')
 def test_invalid_archive_has_no_effect(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d);active=p/'active';active.mkdir();(active/'background.js').write_text('old')
   with self.assertRaises(ValueError):self.module().install(self.package(p,('../escape','bad')),active,p/'backups')
   self.assertEqual((active/'background.js').read_text(),'old');self.assertFalse((p/'escape').exists())
if __name__=='__main__':unittest.main()
