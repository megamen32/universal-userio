#!/usr/bin/env python3
"""Update the existing UserIO extension without touching Chrome profile storage."""
import argparse,json,os,pathlib,stat,subprocess,tempfile,time,urllib.request,zipfile
EXTENSION_ID='aoigpennaaneniokbkochaojddgifamc'
DOWNLOAD='https://msg.bezrabotnyi.com/vk-userio-extension-mv3.zip'
def install(archive,target,backup_root):
 target=pathlib.Path(target).expanduser().resolve();backup_root=pathlib.Path(backup_root).expanduser();backup=None
 if not target.is_dir():raise ValueError('Папка установленного расширения не найдена')
 with zipfile.ZipFile(archive) as z,tempfile.TemporaryDirectory(prefix='userio-update-') as staging:
  names=[i.filename for i in z.infolist() if not i.is_dir()];manifests=[n for n in names if n=='manifest.json' or n.endswith('/manifest.json')]
  if len(manifests)!=1:raise ValueError('В архиве должен быть один manifest.json')
  prefix=manifests[0][:-len('manifest.json')];size=0
  for i in z.infolist():
   p=pathlib.PurePosixPath(i.filename);size+=i.file_size
   if p.is_absolute() or '..' in p.parts or stat.S_ISLNK(i.external_attr>>16) or size>20*1024*1024:raise ValueError('Небезопасный архив')
   if not i.is_dir() and not i.filename.startswith(prefix):raise ValueError('Неоднозначная структура архива')
  manifest=json.loads(z.read(manifests[0]))
  if manifest.get('name')!='Universal UserIO Agent' or manifest.get('manifest_version')!=3:raise ValueError('Это не расширение UserIO')
  old_manifest=target/'manifest.json'
  if old_manifest.exists():
   old=json.loads(old_manifest.read_text())
   if old.get('name')!='Universal UserIO Agent' or old.get('key')!=manifest.get('key'):raise ValueError('Не совпадает идентификатор установленного расширения')
   if tuple(map(int,manifest['version'].split('.')))<tuple(map(int,old['version'].split('.'))):raise ValueError('Архив старее установленной версии; понижение отменено')
  z.extractall(staging);source=pathlib.Path(staging)/prefix
  backup_root.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(backup_root,0o700)
  fd,name=tempfile.mkstemp(prefix=time.strftime('before-%Y%m%d-%H%M%S-'),suffix='.zip',dir=backup_root);os.close(fd);backup=pathlib.Path(name)
  with zipfile.ZipFile(backup,'w',zipfile.ZIP_DEFLATED) as out:
   for p in target.rglob('*'):
    if p.is_symlink():raise ValueError('Символьная ссылка в установленной папке')
    if p.is_file():out.write(p,p.relative_to(target))
  os.chmod(backup,0o600)
  for p in source.rglob('*'):
   if not p.is_file():continue
   relative=p.relative_to(source);destination=target/relative
   if relative.as_posix()=='lib/config.js' and destination.exists():continue
   destination.parent.mkdir(parents=True,exist_ok=True)
   if destination.is_symlink():raise ValueError('Символьная ссылка в папке назначения')
   fd,tmp=tempfile.mkstemp(prefix='.userio-',dir=destination.parent)
   with os.fdopen(fd,'wb') as out:out.write(p.read_bytes())
   os.replace(tmp,destination)
 return {'version':manifest['version'],'target':str(target),'backup':str(backup)}
def installed_path(profile):
 root=pathlib.Path.home()/'Library/Application Support/Google/Chrome'/profile
 for name in ('Secure Preferences','Preferences'):
  p=root/name
  if not p.is_file():continue
  d=json.loads(p.read_text());entry=d.get('extensions',{}).get('settings',{}).get(EXTENSION_ID,{})
  if entry.get('path'):return pathlib.Path(entry['path']).expanduser()
 raise ValueError('Установка не найдена: добавьте папку через chrome://extensions один раз или укажите --target')
def reload_extension():
 # Chrome owns loading/consent. Apple Events refusal is reported, never bypassed.
 url='chrome-extension://'+EXTENSION_ID+'/options.html'
 script='tell application "Google Chrome"\nset t to make new tab at end of tabs of front window with properties {URL:'+json.dumps(url)+'}\ndelay 1\nset resultText to execute t javascript "chrome.runtime.reload(); \'reload_requested\'"\nreturn resultText\nend tell'
 p=subprocess.run(['osascript','-e',script],capture_output=True,text=True,timeout=15)
 return p.returncode==0 and 'reload_requested' in p.stdout

def main():
 parser=argparse.ArgumentParser(description='Обновить UserIO Agent с резервной копией')
 parser.add_argument('--archive',type=pathlib.Path);parser.add_argument('--profile',default='Default');parser.add_argument('--target',type=pathlib.Path);parser.add_argument('--no-reload',action='store_true');args=parser.parse_args()
 target=args.target or installed_path(args.profile)
 with tempfile.TemporaryDirectory(prefix='userio-download-') as d:
  archive=args.archive
  if archive is None:
   archive=pathlib.Path(d)/'extension.zip'
   with urllib.request.urlopen(DOWNLOAD,timeout=30) as r:
    data=r.read(10*1024*1024+1)
   if len(data)>10*1024*1024:raise ValueError('Архив слишком большой')
   archive.write_bytes(data)
  result=install(archive,target,pathlib.Path.home()/'Library/Application Support/UserIO/extension-backups')
 print('Файлы UserIO '+result['version']+' установлены: '+result['target']);print('Резерв: '+result['backup'])
 if not args.no_reload:
  try:ok=reload_extension()
  except (OSError,subprocess.TimeoutExpired):ok=False
  if ok:print('Chrome принял запрос перезагрузки расширения. Рабочую версию подтвердит UserIO.')
  else:
   print('Chrome не подтвердил перезагрузку. Нажмите «Перезагрузить» на карточке UserIO в chrome://extensions.');subprocess.run(['open','-a','Google Chrome','chrome://extensions'],check=False)
if __name__=='__main__':main()
