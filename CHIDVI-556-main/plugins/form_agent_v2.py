"""Memory-aware autonomous form agent prototype for CHIDVI-556."""
from __future__ import annotations
import io,json,re,threading,time,webbrowser
from pathlib import Path
try: import pyautogui
except ImportError: pyautogui=None
from memory.memory_manager import search_memory, update_memory
STATE={"running":False,"task":"","url":"","log":[],"reply":None,"lock":threading.Lock()}

def _key():
    try: return json.loads((Path(__file__).resolve().parent.parent/"config"/"api_keys.json").read_text())['gemini_api_key']
    except: return ''
def _shot():
    b=io.BytesIO(); pyautogui.screenshot().save(b,format='PNG'); return b.getvalue()
def _mem():
    try: return search_memory('name email phone address city college education job company skills birthday preferences form',20)
    except: return 'No stored facts.'
def _save(q,a):
    key='form_'+'_'.join(w for w in re.sub(r'[^a-z0-9 ]',' ',q.lower()).split() if len(w)>2)[:8]
    try: update_memory({'preferences':{key:a}})
    except: pass

def _decide(img):
    from google import genai
    from google.genai import types
    if not _key(): return {'action':'ASK','text':'I need the Gemini API key to inspect the form.'}
    prompt=f'''You are CHIDVI's browser execution agent.
TASK: {STATE['task']}
URL: {STATE['url'] or '(current page)'}
LONG-TERM MEMORY:\n{_mem()}
RECENT ACTIONS:\n{chr(10).join(STATE['log'][-8:]) or '(none)'}

Inspect the screenshot and return ONLY JSON for ONE action: CLICK x,y,reason; TYPE text,reason; KEY key,reason; SCROLL dir,reason; WAIT ms,reason; ASK text,reason; DONE. Use memory only for an exact/clear match. Never invent personal facts. If required info is missing or ambiguous, ASK. For payment, legal/government, purchases, or job/college applications, ASK for confirmation immediately before final submission.'''
    try:
        r=genai.Client(api_key=_key()).models.generate_content(model='gemini-flash-latest',contents=[types.Part.from_bytes(data=img,mime_type='image/png'),prompt])
        return json.loads(re.sub(r'^```(?:json)?|```$','',(r.text or '').strip(),flags=re.M).strip())
    except Exception as e: return {'action':'ASK','text':f'I could not reliably inspect the page: {e}'}

def _say(player,text):
    if player:
        try: player.write_log('AGENT: '+text)
        except: pass
    print('[FORM_AGENT]',text)

def _act(d):
    a=str(d.get('action','')).upper(); reason=str(d.get('reason','')).strip()
    try:
        if a=='CLICK': pyautogui.click(int(d['x']),int(d['y']));time.sleep(.5);return reason or 'Clicked the target.'
        if a=='TYPE': pyautogui.write(str(d.get('text','')));time.sleep(.35);return reason or 'Entered the value.'
        if a=='KEY': k=str(d.get('key','tab'));pyautogui.press(k);time.sleep(.35);return reason or f'Pressed {k}.'
        if a=='SCROLL': dr=str(d.get('dir','down'));pyautogui.scroll(3 if dr=='up' else -3);time.sleep(.5);return reason or f'Scrolled {dr}.'
        if a=='WAIT': time.sleep(max(.1,min(10,int(d.get('ms',1000))/1000)));return reason or 'Waiting for the page.'
        if a=='ASK': return 'ASK:'+str(d.get('text','I need more information.'))
        if a=='DONE': return 'DONE'
        return f'Unsupported action: {a}'
    except Exception as e: return f'Action failed: {e}'

def run(parameters:dict,player=None,session_memory=None):
    a=str(parameters.get('action','start')).lower().strip()
    if a=='start':
        if STATE['running']: return 'The form agent is already working.'
        if pyautogui is None: return "pyautogui isn't installed."
        STATE.update(running=True,task=str(parameters.get('task','complete the requested form')),url=str(parameters.get('url','')),log=[],reply=None)
        if STATE['url']: webbrowser.open(STATE['url'],new=2)
        threading.Thread(target=_loop,args=(player,),daemon=True).start()
        return "On it. I'll inspect the form, use what I remember, narrate each step, and ask you whenever I need information."
    if a=='status': return f"Form agent: {STATE['log'][-1] if STATE['log'] else 'starting page inspection.'}" if STATE['running'] else "The form agent isn't running."
    if a=='answer':
        with STATE['lock']: STATE['reply']=str(parameters.get('text','')).strip()
        return "Got it. I'll continue from that field and remember the answer."
    if a=='stop': STATE['running']=False;return 'Stopped the form agent.'
    return 'Use start, status, answer, or stop.'

def _loop(player):
    last=''
    try:
        while STATE['running']:
            r=_act(_decide(_shot()))
            if r.startswith('ASK:'):
                q=r[4:].strip()
                if q==last: _say(player,"I still need that information, so I've paused safely.");STATE['running']=False;return
                last=q;_say(player,q);STATE['reply']=None;deadline=time.time()+300
                while STATE['running'] and STATE['reply'] is None and time.time()<deadline: time.sleep(.2)
                if not STATE['running']: return
                if STATE['reply'] is None: _say(player,"No answer arrived, so I've paused the form.");STATE['running']=False;return
                ans=STATE['reply'];_save(q,ans);STATE['reply']=None;_say(player,"Thank you. I've stored that answer and will continue.");continue
            if r=='DONE': _say(player,'The requested form task is complete.');STATE['running']=False;return
            STATE['log'].append(r);_say(player,r)
    except Exception as e: _say(player,f'The form agent stopped safely because of an error: {e}')
    finally: STATE['running']=False;STATE['reply']=None

PLUGIN={'name':'form_agent_v2','description':'Memory-aware autonomous form filling with URL opening, screen inspection, narration, user questions, answer persistence, and resume.','parameters':{'type':'OBJECT','properties':{'action':{'type':'STRING'},'url':{'type':'STRING'},'task':{'type':'STRING'},'text':{'type':'STRING'}},'required':[]}}
