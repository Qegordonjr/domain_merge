#!/usr/bin/env python3
"""
Jira DC Migration GUI  ·  April 2025  (Layout + BooleanVar patch)
────────────────────────────────────────────────────────────────
• Right-hand options panel (widgets disabled/enabled, never vanish)
• Logs/ folder auto-created; per-user logs to Logs/<source>.log
• Dry-run toggle
• Picker migrations each have “Unresolved only”
• FIX: BooleanVar is no longer used as dict key (avoids TypeError)
"""

import os, sys, csv, queue, threading, logging, requests, urllib3
from datetime import datetime, timezone
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ──────────────── logging set-up ────────────────
ROOT = logging.getLogger()
ROOT.setLevel(logging.INFO)
FMT = logging.Formatter("%(asctime)s %(levelname)s - %(message)s")
ROOT.addHandler(logging.StreamHandler(sys.stdout))

class QueueHandler(logging.Handler):
    def __init__(self,q): super().__init__(); self.q=q
    def emit(self,rec): self.q.put(self.format(rec))

queue_handler=None
def ensure_log_dir():
    path=os.path.join(os.path.dirname(__file__),"Logs")
    os.makedirs(path,exist_ok=True); return path

# ─────────────── Jira helper functions (unchanged logic) ───────────────
def migr_groups(base,u,pw,src,tgt,exclude,dry):
    lg=logging.getLogger("groups")
    skip={g.strip() for g in exclude.split(",") if g.strip()}
    s=requests.Session(); s.auth=(u,pw); s.verify=False
    r=s.get(f"{base}/rest/api/2/user",params={"username":src,"expand":"groups"})
    if r.status_code!=200: lg.error("groups fetch %s %s",r.status_code,r.text[:120]); return
    for g in r.json()["groups"]["items"]:
        name=g["name"]
        if name in skip: lg.info("skip group %s",name); continue
        if dry: lg.info("[dry] add %s→%s",tgt,name); continue
        resp=s.post(f"{base}/rest/api/2/group/user?groupname={name}",json={"name":tgt})
        lg.info("%s → %s (%s)",name,tgt,resp.status_code)

def migr_filters(base,u,pw,fcsv,src,tgt,dry):
    lg=logging.getLogger("filters")
    if not os.path.isfile(fcsv): lg.error("filter CSV missing: %s",fcsv); return
    s=requests.Session(); s.auth=(u,pw); s.verify=False
    for fid,owner,*_ in csv.reader(open(fcsv,encoding="utf-8-sig")):
        if owner!=src: continue
        if dry: lg.info("[dry] filter %s owner→%s",fid,tgt); continue
        url=f"{base}/rest/api/2/filter/{fid}"
        data=s.get(url).json(); data["owner"]={"name":tgt}
        r=s.put(url,params={"overrideSharePermissions":"true"},json=data)
        lg.info("filter %s status %s",fid,r.status_code)

def migr_issues(base,u,pw,src,tgt,unres,dry):
    lg=logging.getLogger("issues")
    s=requests.Session(); s.auth=(u,pw); s.verify=False
    jql=f'(assignee="{src}" OR reporter="{src}")'
    if unres: jql+=' AND resolution=Unresolved'
    start=0
    while True:
        r=s.post(f"{base}/rest/api/2/search",
                 json={"jql":jql,"startAt":start,"maxResults":100,
                       "fields":["assignee","reporter"]})
        if r.status_code!=200: lg.error("search err %s",r.status_code); break
        d=r.json(); issues=d["issues"]
        if not issues: break
        for it in issues:
            key=it["key"]; upd={}
            if it["fields"]["assignee"] and it["fields"]["assignee"]["name"]==src:
                upd["assignee"]={"name":tgt}
            if it["fields"]["reporter"] and it["fields"]["reporter"]["name"]==src:
                upd["reporter"]={"name":tgt}
            if not upd: continue
            if dry: lg.info("[dry] issue %s",key); continue
            s.put(f"{base}/rest/api/2/issue/{key}?notifyUsers=false",
                  json={"fields":upd},
                  headers={"Content-Type":"application/json"})
            lg.info("issue %s updated",key)
        start+=len(issues)
        if start>=d["total"]: break

def fast_roles(sess,base,rcsv,src,tgt,dry):
    lg=logging.getLogger("roles")
    if not os.path.isfile(rcsv): lg.error("roles CSV missing: %s",rcsv); return
    for row in csv.DictReader(open(rcsv,encoding="utf-8")):
        if src not in row["usernames"].split(";"): continue
        if dry: lg.info("[dry] role %s/%s",row["project_key"],row["role_name"]); continue
        r=sess.post(row["role_url"],json={"user":[tgt]})
        lg.info("%s/%s → %s (%s)",row["project_key"],row["role_name"],tgt,r.status_code)

def single_picker(sess,base,src,tgt,unres,dry):
    lg=logging.getLogger("singleCF")
    fields=[f for f in sess.get(f"{base}/rest/api/2/field").json()
            if f.get("custom") and f["schema"]["custom"].endswith(":userpicker")]
    for f in fields:
        fid=f["id"]; cf=fid.replace("customfield_","")
        jql=f'cf[{cf}] = "{src}"'
        if unres: jql+=' AND resolution=Unresolved'
        r=sess.get(f"{base}/rest/api/2/search",
                   params={"jql":jql,"fields":[fid],"maxResults":500})
        if r.status_code!=200: continue
        for it in r.json()["issues"]:
            key=it["key"]
            if dry: lg.info("[dry] %s on %s",fid,key); continue
            sess.put(f"{base}/rest/api/2/issue/{key}?notifyUsers=false",
                     json={"fields":{fid:{"name":tgt}}},
                     headers={"Content-Type":"application/json"})
            lg.info("%s → %s on %s",fid,tgt,key)

def multi_picker(sess,base,src,tgt,unres,dry):
    lg=logging.getLogger("multiCF")
    fields=[f for f in sess.get(f"{base}/rest/api/2/field").json()
            if f.get("custom") and f["schema"]["custom"].endswith(":multiuserpicker")]
    for f in fields:
        fid=f["id"]; cf=fid.replace("customfield_","")
        jql=f'cf[{cf}] = "{src}"'
        if unres: jql+=' AND resolution=Unresolved'
        r=sess.get(f"{base}/rest/api/2/search",
                   params={"jql":jql,"fields":[fid],"maxResults":500})
        if r.status_code!=200: continue
        for it in r.json()["issues"]:
            key=it["key"]; cur=it["fields"][fid] or []
            names=[u["name"] for u in cur if u["name"]!=src]
            if tgt not in names: names.append(tgt)
            if dry: lg.info("[dry] %s on %s",fid,key); continue
            sess.put(f"{base}/rest/api/2/issue/{key}?notifyUsers=false",
                     json={"fields":{fid:[{"name":n} for n in names]}},
                     headers={"Content-Type":"application/json"})
            lg.info("%s updated on %s",fid,key)

# ───────────────────── GUI class ─────────────────────
class MigrationGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Jira DC Migration GUI – April 2025 patch")
        self.geometry("1180x900")

        self.q=queue.Queue()
        global queue_handler
        if queue_handler is None:
            queue_handler=QueueHandler(self.q); queue_handler.setFormatter(FMT)
            ROOT.addHandler(queue_handler)

        self.logs_dir=ensure_log_dir()

        # form vars
        self.url=tk.StringVar(); self.adm=tk.StringVar(); self.pw=tk.StringVar()
        self.src=tk.StringVar(); self.tgt=tk.StringVar(); self.multi_csv=tk.StringVar()
        self.dry=tk.BooleanVar(value=True); self.multi_mode=tk.BooleanVar()

        # feature vars + sub-options
        self.v_group=tk.BooleanVar();  self.exclude=tk.StringVar()
        self.v_filter=tk.BooleanVar(); self.filter_csv=tk.StringVar()
        self.v_issue=tk.BooleanVar();  self.issue_unres=tk.BooleanVar(value=True)
        self.v_roles=tk.BooleanVar();  self.roles_csv=tk.StringVar()
        self.v_single=tk.BooleanVar(); self.single_unres=tk.BooleanVar(value=True)
        self.v_multi=tk.BooleanVar();  self.multi_unres=tk.BooleanVar(value=True)

        self._build(); self.after(200,self._pump)

    # ───── build UI ─────
    def _build(self):
        left=tk.Frame(self); right=tk.Frame(self,relief="groove",bd=2)
        left.pack(side="left",fill="both",expand=True,padx=6,pady=6)
        right.pack(side="right",fill="y",padx=6,pady=6)

        r=0
        for lbl,var,w,mask in (("Jira URL",self.url,70,None),
                               ("Admin user",self.adm,40,None),
                               ("Password",self.pw,40,"*")):
            tk.Label(left,text=lbl).grid(row=r,column=0,sticky="e")
            tk.Entry(left,textvariable=var,width=w,show=mask
                     ).grid(row=r,column=1,sticky="w"); r+=1

        tk.Checkbutton(left,text="Dry-run",variable=self.dry
                       ).grid(row=r,column=0,sticky="w"); r+=1
        tk.Checkbutton(left,text="Multi-user (CSV)",variable=self.multi_mode,
                       command=self._toggle_multi
                       ).grid(row=r,column=0,sticky="w"); r+=1

        self.l_src=tk.Label(left,text="Source user"); self.e_src=tk.Entry(left,textvariable=self.src,width=35)
        self.l_tgt=tk.Label(left,text="Target user"); self.e_tgt=tk.Entry(left,textvariable=self.tgt,width=35)
        self.l_src.grid(row=r,column=0,sticky="e"); self.e_src.grid(row=r,column=1,sticky="w"); r+=1
        self.l_tgt.grid(row=r,column=0,sticky="e"); self.e_tgt.grid(row=r,column=1,sticky="w"); r+=1

        self.b_csv=tk.Button(left,text="Choose CSV",
                             command=lambda:self._pick(self.multi_csv))
        self.lab_csv=tk.Label(left,textvariable=self.multi_csv)

        tk.Button(left,text="START",bg="#3a9",fg="white",
                  command=self._start,padx=20
                 ).grid(row=r,column=0,columnspan=2,pady=8); r+=1
        self.log=scrolledtext.ScrolledText(left,width=100,height=28)
        self.log.grid(row=r,column=0,columnspan=2,pady=6)

        # ---- right panel ----
        tk.Label(right,text="Features / Options",
                 font=("Segoe UI",11,"bold")).pack(anchor="w",pady=(4,8))

        def add_row(label_var, text):
            frm=tk.Frame(right); frm.pack(anchor="w",fill="x",pady=2)
            chk=tk.Checkbutton(frm,text=text,variable=label_var,
                               command=self._sync)
            chk.pack(side="left"); sub=tk.Frame(frm); sub.pack(side="left",padx=10)
            return sub

        sub_grp = add_row(self.v_group,"Groups")
        tk.Label(sub_grp,text="Exclude:").pack(side="left")
        ent_ex=tk.Entry(sub_grp,textvariable=self.exclude,width=18); ent_ex.pack(side="left")

        sub_fil = add_row(self.v_filter,"Filters")
        btn_fil=tk.Button(sub_fil,text="Filter CSV",command=lambda:self._pick(self.filter_csv))
        btn_fil.pack(side="left")
        tk.Label(sub_fil,textvariable=self.filter_csv,width=26,anchor="w").pack(side="left")

        sub_iss = add_row(self.v_issue,"Issues (assignee/reporter)")
        tk.Checkbutton(sub_iss,text="Unresolved only",
                       variable=self.issue_unres).pack(side="left")

        sub_role= add_row(self.v_roles,"Roles")
        btn_role=tk.Button(sub_role,text="Roles CSV",command=lambda:self._pick(self.roles_csv))
        btn_role.pack(side="left")
        tk.Label(sub_role,textvariable=self.roles_csv,width=26,anchor="w").pack(side="left")

        sub_single = add_row(self.v_single,"Single user-pickers")
        tk.Checkbutton(sub_single,text="Unresolved only",
                       variable=self.single_unres).pack(side="left")

        sub_multi = add_row(self.v_multi,"Multi user-pickers")
        tk.Checkbutton(sub_multi,text="Unresolved only",
                       variable=self.multi_unres).pack(side="left")

        # keep for enable/disable
        self.sub_pairs=[
            (self.v_group,[ent_ex]),
            (self.v_filter,[btn_fil]),
            (self.v_issue,[ ]),
            (self.v_roles,[btn_role]),
            (self.v_single,[ ]),
            (self.v_multi,[ ]),
        ]
        self._sync()   # set initial disabled states

    def _pick(self,var):
        p=filedialog.askopenfilename(filetypes=[("CSV","*.csv")])
        if p: var.set(p)

    def _toggle_multi(self):
        if self.multi_mode.get():
            self.l_src.grid_remove(); self.e_src.grid_remove()
            self.l_tgt.grid_remove(); self.e_tgt.grid_remove()
            self.b_csv.grid(row=5,column=0,sticky="w")
            self.lab_csv.grid(row=5,column=1,sticky="w")
        else:
            self.b_csv.grid_remove(); self.lab_csv.grid_remove()
            self.l_src.grid(); self.e_src.grid()
            self.l_tgt.grid(); self.e_tgt.grid()

    def _sync(self):
        for v,widgets in self.sub_pairs:
            state="normal" if v.get() else "disabled"
            for w in widgets:
                try: w.configure(state=state)
                except tk.TclError: pass

    # ───── run button ─────
    def _start(self):
        if not (self.url.get() and self.adm.get() and self.pw.get()):
            messagebox.showerror("Missing","URL / admin / password"); return
        pairs=[]
        if self.multi_mode.get():
            if not os.path.isfile(self.multi_csv.get()):
                messagebox.showerror("CSV","Select CSV"); return
            with open(self.multi_csv.get(),newline='',encoding="utf-8-sig") as f:
                for r in csv.reader(f):
                    if len(r)<2 or r[0].lower().startswith("source"): continue
                    pairs.append((r[0].strip(),r[1].strip()))
        else:
            if not (self.src.get() and self.tgt.get()):
                messagebox.showerror("Missing","source / target"); return
            pairs=[(self.src.get().strip(),self.tgt.get().strip())]
        threading.Thread(target=self._worker,args=(pairs,),daemon=True).start()
        ROOT.info("thread started for %d pair(s)",len(pairs))

    # ───── background migration ─────
    def _worker(self,pairs):
        sess=requests.Session(); sess.auth=(self.adm.get(),self.pw.get()); sess.verify=False
        for src,tgt in pairs:
            fh=logging.FileHandler(os.path.join(self.logs_dir,f"{src}.log"),
                                   mode="a",encoding="utf-8"); fh.setFormatter(FMT)
            ROOT.addHandler(fh)
            ROOT.info("=== %s → %s (%s UTC) ===",src,tgt,datetime.now(timezone.utc).strftime('%H:%M:%S'))

            if self.v_group.get():
                migr_groups(self.url.get(),self.adm.get(),self.pw.get(),
                            src,tgt,self.exclude.get(),self.dry.get())
            if self.v_filter.get():
                migr_filters(self.url.get(),self.adm.get(),self.pw.get(),
                             self.filter_csv.get(),src,tgt,self.dry.get())
            if self.v_issue.get():
                migr_issues(self.url.get(),self.adm.get(),self.pw.get(),
                            src,tgt,self.issue_unres.get(),self.dry.get())
            if self.v_roles.get():
                fast_roles(sess,self.url.get(),self.roles_csv.get(),
                           src,tgt,self.dry.get())
            if self.v_single.get():
                single_picker(sess,self.url.get(),src,tgt,
                              self.single_unres.get(),self.dry.get())
            if self.v_multi.get():
                multi_picker(sess,self.url.get(),src,tgt,
                             self.multi_unres.get(),self.dry.get())

            ROOT.info("done (%s)",datetime.now(timezone.utc).strftime('%H:%M:%S'))
            ROOT.removeHandler(fh); fh.close()
        ROOT.info("ALL completed")

    # ───── queue pump ─────
    def _pump(self):
        try:
            while True:
                self.log.insert("end",queue_handler.q.get_nowait()+"\n"); self.log.see("end")
        except queue.Empty: pass
        self.after(200,self._pump)

# ─────────────────────────────────────────────
if __name__=="__main__":
    MigrationGUI().mainloop()
