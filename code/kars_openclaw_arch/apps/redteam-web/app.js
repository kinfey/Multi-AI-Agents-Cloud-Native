const COPY={
	cn:{locale:'zh-CN',docTitle:'KARS 红队观测台',eyebrow:'RED TEAM OBSERVATORY',
			title:'财经 Agent<br>安全观测台',waiting:'等待最近一次评估',
			passed:'通过安全门槛',failed:'需要处理后复测',unavailable:'报告服务不可用',
			findings:'发现记录',runs:'运行历史',refresh:'刷新',emptyFindings:'本次评估没有发现记录',
			noRuns:'暂无评估记录',metrics:['通过率','测试用例','发现记录'],
			viewHint:'点击查看',
			result:{pass:'通过',fail:'失败'},
			severity:{critical:'严重',high:'高',medium:'中',low:'低'},
			status:{open:'待处理',accepted:'已接受',closed:'已关闭'}},
	en:{locale:'en-US',docTitle:'KARS Red-team Observatory',eyebrow:'RED TEAM OBSERVATORY',
			title:'FINANCE AGENT<br>SECURITY OBSERVATORY',waiting:'Waiting for the latest evaluation',
			passed:'Security gate passed',failed:'Remediation and retest required',
			unavailable:'Report service unavailable',findings:'Findings',runs:'Run history',
			refresh:'Refresh',emptyFindings:'No findings in this evaluation',noRuns:'No evaluation runs yet',
			metrics:['PASS RATE','TEST CASES','FINDINGS'],viewHint:'Click to view',
			result:{pass:'PASS',fail:'FAIL'},
			severity:{critical:'CRITICAL',high:'HIGH',medium:'MEDIUM',low:'LOW'},
			status:{open:'OPEN',accepted:'ACCEPTED',closed:'CLOSED'}}
};
const state={reports:[],language:'cn',loadFailed:false,selectedRunId:null};
const metrics=document.querySelector('#metrics');
const findings=document.querySelector('#finding-list');
const runs=document.querySelector('#runs');

function copy(){return COPY[state.language];}
function localText(item,field){
	if(state.language==='cn')return item[`${field}_zh`]||item[`${field}_cn`]||item[field];
	return item[`${field}_en`]||item[field];
}
function formatDate(value){
	const date=new Date(value);
	return Number.isNaN(date.valueOf())?value:new Intl.DateTimeFormat(copy().locale,{dateStyle:'medium',timeStyle:'short'}).format(date);
}
function renderChrome(){
	const text=copy();
	document.documentElement.lang=state.language==='en'?'en':'zh-CN';
	document.title=text.docTitle;
	document.querySelector('#eyebrow').textContent=text.eyebrow;
	document.querySelector('#page-title').innerHTML=text.title;
	document.querySelector('#findings-title').textContent=text.findings;
	document.querySelector('#runs-title').textContent=text.runs;
	const refresh=document.querySelector('#refresh');
	refresh.title=text.refresh;refresh.setAttribute('aria-label',text.refresh);
	document.querySelector('#clock').textContent=new Intl.DateTimeFormat(text.locale,{dateStyle:'medium'}).format(new Date());
}
function selectedReport(){
	if(!state.reports.length)return null;
	return state.reports.find(report=>report.run_id===state.selectedRunId)||state.reports[0];
}
function render(){
	const text=copy();const latest=selectedReport();
	runs.replaceChildren();
	state.reports.forEach(report=>{
		const item=document.createElement('div');item.className='run';
		if(latest&&report.run_id===latest.run_id)item.classList.add('active');
		item.tabIndex=0;item.setAttribute('role','button');item.dataset.runId=report.run_id;
		item.innerHTML=`<b>${escapeHtml(report.run_id)}</b><span>${escapeHtml(formatDate(report.created_at))} · ${text.viewHint}</span>`;
		runs.append(item);
	});
	if(!state.reports.length){
		runs.textContent=text.noRuns;metrics.replaceChildren();findings.textContent=text.emptyFindings;
		document.querySelector('#verdict').textContent=state.loadFailed?text.unavailable:text.waiting;return;
	}
	document.querySelector('#verdict').textContent=latest.passed?text.passed:text.failed;
	const values=[[text.metrics[0],`${Math.round(latest.pass_rate*100)}%`],[text.metrics[1],latest.total_cases],[text.metrics[2],latest.findings.length]];
	metrics.innerHTML=values.map(([label,value])=>`<div class="metric"><b>${value}</b><span>${label}</span></div>`).join('');
	findings.replaceChildren();
	if(!latest.findings.length){findings.textContent=text.emptyFindings;return;}
	latest.findings.forEach(finding=>{
		const row=document.createElement('div');row.className='finding';
		const severity=text.severity[finding.severity]||finding.severity.toUpperCase();
		const status=text.status[finding.status]||finding.status;
		row.innerHTML=`<span class="severity ${finding.severity}">${escapeHtml(severity)}</span><span><b>${escapeHtml(localText(finding,'title'))}</b><em>${escapeHtml(status)}</em></span><small>${escapeHtml(finding.corpus)} / ${escapeHtml(finding.id)}</small>`;
		findings.append(row);
	});
}
async function load(){
	try{
		const response=await fetch('/api/reports',{cache:'no-store'});
		if(!response.ok)throw new Error(`HTTP ${response.status}`);
		state.reports=await response.json();state.loadFailed=false;
		if(!state.reports.some(report=>report.run_id===state.selectedRunId)){
			state.selectedRunId=state.reports.length?state.reports[0].run_id:null;
		}
	}catch(error){state.reports=[];state.loadFailed=true;console.error(error);}
	render();
}
function switchLanguage(language){
	if(language===state.language)return;
	state.language=language;
	document.querySelectorAll('#lang-switch button').forEach(button=>{
		const active=button.dataset.lang===language;
		button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));
	});
	renderChrome();render();
}
function escapeHtml(value){const node=document.createElement('span');node.textContent=String(value);return node.innerHTML;}
document.querySelector('#lang-switch').addEventListener('click',event=>{
	const button=event.target.closest('button[data-lang]');if(button)switchLanguage(button.dataset.lang);
});
document.querySelector('#refresh').addEventListener('click',load);
function selectRun(runId){
	if(!runId||runId===state.selectedRunId)return;
	state.selectedRunId=runId;render();
}
runs.addEventListener('click',event=>{
	const item=event.target.closest('.run[data-run-id]');if(item)selectRun(item.dataset.runId);
});
runs.addEventListener('keydown',event=>{
	if(event.key!=='Enter'&&event.key!==' ')return;
	const item=event.target.closest('.run[data-run-id]');if(item){event.preventDefault();selectRun(item.dataset.runId);}
});
renderChrome();load();
