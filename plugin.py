import socket
import threading
import time

class Plugin:
    DEFAULTS = {
        'port1': 10111,
        'port2': 10112,
        'bindAddress': '0.0.0.0',
        'sourceTimeout': 60,
        'name1': 'AvNav 1',
        'name2': 'AvNav 2'
    }

    @classmethod
    def pluginInfo(cls):
        return {'description': 'Merge AIS from two UDP inputs and retain per-MMSI source provenance'}

    def __init__(self, api):
        self.api=api
        self.lock=threading.RLock()
        self.targets={}
        self.fragments={1:{},2:{}}
        self.counts={1:0,2:0}
        self.sockets=[]
        self.threads=[]
        self.stop=False
        self.cfg=dict(self.DEFAULTS)
        for k,v in list(self.cfg.items()):
            try:
                self.cfg[k]=api.getConfigValue(k, v)
            except Exception:
                pass

        try:
            api.registerRequestHandler(self.handleApiRequest)
        except TypeError:
            api.registerRequestHandler('aisSourceMerge', self.handleApiRequest)
        try:
            api.registerUserApp(api.getBaseUrl() + '/index.html', 'icon.png', 'AIS Sources')
        except Exception as e:
            api.log('AIS Source Merge: user app registration skipped: %s' % e)
        try:
            params=[
              {'name':'port1','type':'NUMBER','default':10111,'description':'UDP port for AvNav 1'},
              {'name':'port2','type':'NUMBER','default':10112,'description':'UDP port for AvNav 2'},
              {'name':'sourceTimeout','type':'NUMBER','default':60,'description':'Seconds before a source becomes stale'},
              {'name':'name1','type':'STRING','default':'AvNav 1','description':'Display name for source 1'},
              {'name':'name2','type':'STRING','default':'AvNav 2','description':'Display name for source 2'}]
            api.registerEditableParameters(params, self.changeConfig)
        except Exception as e:
            api.log('AIS Source Merge: editable settings unavailable: %s' % e)
        try:
            api.registerRestart(self.requestStop)
        except Exception:
            pass

    def changeConfig(self, values):
        self.api.saveConfigValues(values)
        self.cfg.update(values)
        return True

    def requestStop(self):
        self.stop=True
        for s in self.sockets:
            try: s.close()
            except Exception: pass

    @staticmethod
    def _checksum_ok(line):
        if '*' not in line: return True
        body, given=line.rsplit('*',1)
        body=body[1:] if body and body[0] in '!$' else body
        c=0
        for ch in body: c ^= ord(ch)
        try: return c == int(given[:2],16)
        except Exception: return False

    @staticmethod
    def _sixbit(payload):
        bits=''
        for ch in payload:
            v=ord(ch)-48
            if v>40: v-=8
            if v<0 or v>63: return None
            bits += format(v,'06b')
        return bits


    @staticmethod
    def _signed(value, width):
        sign = 1 << (width - 1)
        return value - (1 << width) if value & sign else value

    def _position(self, payload):
        bits=self._sixbit(payload)
        if not bits or len(bits) < 38: return None
        try:
            msg=int(bits[0:6],2)
            if msg in (1,2,3):
                if len(bits) < 116: return None
                lon=self._signed(int(bits[61:89],2),28)/600000.0
                lat=self._signed(int(bits[89:116],2),27)/600000.0
            elif msg in (18,19):
                if len(bits) < 112: return None
                lon=self._signed(int(bits[57:85],2),28)/600000.0
                lat=self._signed(int(bits[85:112],2),27)/600000.0
            elif msg == 27:
                if len(bits) < 79: return None
                lon=self._signed(int(bits[44:62],2),18)/600.0
                lat=self._signed(int(bits[62:79],2),17)/600.0
            else: return None
            if abs(lon) > 180 or abs(lat) > 90: return None
            return lat,lon
        except Exception: return None

    def _mmsi(self, payload):
        bits=self._sixbit(payload)
        if not bits or len(bits)<38: return None
        try: return int(bits[8:38],2)
        except Exception: return None

    def _process(self, line, source_id):
        line=line.strip()
        if not line.startswith(('!AIVDM','!AIVDO')) or not self._checksum_ok(line): return
        self.counts[source_id] += 1
        raw=line.split('*',1)[0]
        p=raw.split(',')
        if len(p)<7: return
        try:
            total=int(p[1]); num=int(p[2])
        except Exception: return
        seq=p[3] or '-'; chan=p[4] or '-'; payload=p[5]
        full=payload
        now=time.time()
        if total>1:
            key=(seq,chan,total)
            state=self.fragments[source_id].setdefault(key, {'t':now,'parts':{}})
            state['t']=now; state['parts'][num]=payload
            if len(state['parts'])<total: return
            try: full=''.join(state['parts'][i] for i in range(1,total+1))
            except KeyError: return
            del self.fragments[source_id][key]
        mmsi=self._mmsi(full)
        if mmsi:
            with self.lock:
                rec=self.targets.setdefault(str(mmsi), {'mmsi':mmsi,'seen':{},'positions':{}})
                rec['seen'][str(source_id)]=now
                pos=self._position(full)
                if pos is not None:
                    rec['positions'][str(source_id)]={'lat':pos[0],'lon':pos[1],'unix':now}
                    rec['lat']=pos[0]; rec['lon']=pos[1]
        # Preserve the source as the AvNav channel name; standard AIS decoding still receives the telegram.
        self.api.addNMEA(line, source=self.cfg['name%d'%source_id])


    def _listen(self, source_id, port):
        s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        s.bind((str(self.cfg['bindAddress']),int(port)))
        s.settimeout(1.0); self.sockets.append(s)
        self.api.log('AIS Source Merge listening for %s on UDP %s' % (self.cfg['name%d'%source_id],port))
        while not self.stop:
            try: data,_=s.recvfrom(65535)
            except socket.timeout: continue
            except OSError: break
            for line in data.decode('ascii','ignore').replace('\r','\n').split('\n'):
                if line.strip():
                    try: self._process(line,source_id)
                    except Exception as e: self.api.error('AIS Source Merge input error: %s'%e)

    def _snapshot(self):
        now=time.time(); timeout=float(self.cfg['sourceTimeout'])
        out=[]
        with self.lock:
            for m,rec in list(self.targets.items()):
                active=[]; seen={}
                for sid in ('1','2'):
                    t=rec['seen'].get(sid)
                    if t is not None:
                        age=now-t; seen[sid]={'unix':t,'age':round(age,1)}
                        if age<=timeout: active.append(sid)
                if not active and all(now-v.get('unix',0)>timeout*10 for v in seen.values()):
                    del self.targets[m]; continue
                names=[self.cfg['name%s'%sid] for sid in active]
                item={'mmsi':rec['mmsi'],'source':' + '.join(names) if names else 'stale','active':active,'seen':seen,'positions':rec.get('positions',{})}
                if 'lat' in rec and 'lon' in rec:
                    item['lat']=rec['lat']; item['lon']=rec['lon']
                out.append(item)
        out.sort(key=lambda x:x['mmsi'])
        return out

    def handleApiRequest(self, *args, **kwargs):
        return {'status':'OK','timeout':self.cfg['sourceTimeout'],'targets':self._snapshot(),
                'sources':{'1':self.cfg['name1'],'2':self.cfg['name2']},'counts':self.counts}

    def run(self):
        try:
            for sid in (1,2):
                t=threading.Thread(target=self._listen,args=(sid,int(self.cfg['port%d'%sid])),daemon=True)
                self.threads.append(t); t.start()
            self.api.setStatus('RUNNING','UDP %s / %s' % (self.cfg['port1'],self.cfg['port2']))
            while not self.stop and not self.api.shouldStopMainThread():
                self.api.setStatus('RUNNING','UDP %s/%s; AIS %s=%s, %s=%s; targets=%s' % (self.cfg['port1'],self.cfg['port2'],self.cfg['name1'],self.counts[1],self.cfg['name2'],self.counts[2],len(self.targets)))
                time.sleep(1)
        except Exception as e:
            self.api.setStatus('ERROR',str(e))
            raise
        finally:
            self.requestStop()



