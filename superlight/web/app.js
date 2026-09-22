"use strict";
const $ = id => document.getElementById(id);
const native = action => { if (window.webkit?.messageHandlers?.superlight) { window.webkit.messageHandlers.superlight.postMessage(action); return true; } return false; };
const words = {
  en:{connecting:"Connecting",online:"Collector online",offline:"Collector offline",connect:"Connect Codex",overview:"Overview",rates:"Model rates",day:"Today",week:"This week",month:"This month",estimate:"Estimated cost · USD",activity:"Spending activity",estimated:"Estimated cost",byModel:"By model",modelHint:"A breakdown of the selected period.",export:"Export ↗",model:"Model",input:"Input",cached:"Cached",output:"Output",cost:"Est. cost",emptyTitle:"No usage yet",emptyHint:"Connect Codex and finish a response to see your first record.",emptyPeriod:"No usage recorded in this period.",cacheNote:"Cached tokens are part of input, not extra tokens.",referenceRates:"Reference rates",rateUnit:"USD per 1 million tokens",longContext:"Long context",howCost:"Cost calculation",formula:"Uncached input × input rate + cached input × cache rate + output × output rate.",exclusions:"Excludes tools, Fast mode, regional uplifts and cache-write premiums. Unknown prices stay unknown.",waiting:"Waiting for the collector…",refresh:"Refresh",openDashboard:"Open dashboard ↗",oneTime:"CODEX SETUP",setupTitle:"Connect Codex",setupHint:"Add this section to your user config at ~/.codex/config.toml. Merge an existing [otel] section instead of adding it twice.",copyConfig:"Copy configuration",setupAfter:"Restart Codex, then complete a local task. Your existing login and model connection stay the same.",setupScope:"Local Codex tasks only. Remote tasks need a collector on their execution host.",copied:"Configuration copied",copyFailed:"Select and copy the configuration above.",tokens:"tokens",responses:"responses",updated:"Updated",asOf:"Price snapshot",unknown:"Unknown",error:"Cannot reach SuperLight. Start the collector, then refresh.",standard:"Standard rate",threshold:"input tokens",partialLabel:"Partial estimate",exported:"Summary exported",noData:"No recorded usage",settings:"Collector settings"},
  zh:{connecting:"正在连接",online:"服务运行中",offline:"服务未连接",connect:"接入 Codex",overview:"用量总览",rates:"模型单价",day:"今日",week:"本周",month:"本月",estimate:"估算成本 · USD",activity:"消费走势",estimated:"估算消费",byModel:"模型明细",modelHint:"所选时段的用量与参考成本。",export:"导出 ↗",model:"模型",input:"输入",cached:"缓存",output:"输出",cost:"估算金额",emptyTitle:"暂无用量",emptyHint:"接入 Codex，完成一次响应后即可查看用量。",emptyPeriod:"这个时段还没有用量记录。",cacheNote:"缓存已计入输入，不额外累加。",referenceRates:"模型参考单价",rateUnit:"每百万 Token 的美元价格",longContext:"长上下文",howCost:"费用计算",formula:"非缓存输入 × 输入单价 + 缓存输入 × 缓存单价 + 输出 × 输出单价。",exclusions:"不含工具调用、Fast 模式、区域附加费和缓存写入溢价。未知单价不会记为零。",waiting:"等待接收器…",refresh:"刷新",openDashboard:"打开完整面板 ↗",oneTime:"CODEX 配置",setupTitle:"接入 Codex",setupHint:"将以下内容加入用户配置 ~/.codex/config.toml。如果已有 [otel]，请合并修改，不要重复添加。",copyConfig:"复制配置",setupAfter:"重启 Codex，然后完成一次本机任务。沿用现有登录和模型网络连接。",setupScope:"适用于本机 Codex 任务。远程任务需在执行主机配置接收器。",copied:"配置已复制",copyFailed:"请选中上方配置并复制。",tokens:"Token",responses:"次响应",updated:"更新于",asOf:"价格核对日期",unknown:"未知",error:"暂时无法连接 SuperLight。请启动接收器后刷新。",standard:"标准单价",threshold:"输入 Token",partialLabel:"部分估算",exported:"汇总已导出",noData:"尚无用量记录",settings:"接收器设置"}
};
let lang = localStorage.getItem("superlight.language") || (navigator.language.startsWith("zh") ? "zh" : "en");
if (!words[lang]) lang = "en";
let data = null, period = "day", loading = false;
const compact = new URLSearchParams(location.search).get("compact") === "1";
document.documentElement.classList.toggle("compact", compact);
const t = key => words[lang][key] || key;
const number = value => new Intl.NumberFormat(lang === "zh" ? "zh-CN" : "en-US", {notation:"compact",maximumFractionDigits:1}).format(value || 0);
const money = (value, precise=false) => {
  if(value === null || value === undefined)return "—";
  const n=Number(value), digits=precise?6:(n>0&&n<.1?4:2);
  if(n>0&&n<10**(-digits))return "<$"+(10**(-digits)).toFixed(digits);
  return "$"+new Intl.NumberFormat("en-US",{minimumFractionDigits:2,maximumFractionDigits:digits}).format(n);
};
const element = (tag, text, cls) => { const e = document.createElement(tag); if(text !== undefined) e.textContent = text; if(cls) e.className=cls; return e; };
function translate(){
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  document.querySelectorAll("[data-i18n]").forEach(e=>e.textContent=t(e.dataset.i18n));
  $("language").textContent = lang === "zh" ? "EN" : "中文";
  $("settings").title = t("settings"); $("settings").setAttribute("aria-label",t("settings"));
  if(data){render();$("status-text").textContent=t($("status-dot").classList.contains("online")?"online":"offline");}
}
function modelCell(model, subtitle){
  const cell=element("td"), wrap=element("div",undefined,"model-name");
  wrap.append(element("span",model.startsWith("gpt-")?"AI":model.slice(0,2).toUpperCase(),"model-icon"));
  const name=element("span",model); if(subtitle) name.append(element("span",subtitle,"model-subtitle"));
  wrap.append(name);cell.append(wrap);return cell;
}
function render(){
  for(const p of ["day","week","month"]){const a=data.periods[p].totals;$(p+"-cost").textContent=money(a.estimated_cost_usd)+(a.unpriced_responses?"*":"");$(p+"-cost").title=a.unpriced_responses?t("partialLabel"):"USD";$(p+"-tokens").textContent=number(a.total_tokens)+" "+t("tokens");}
  const selected=data.periods[period], totals=selected.totals;
  $("model-rows").replaceChildren();
  selected.models.forEach(m=>{const row=element("tr");row.append(modelCell(m.model));for(const field of ["input_tokens","cached_input_tokens","output_tokens"]){const cell=element("td",number(m[field]));cell.title=String(m[field]);if(field==="cached_input_tokens"&&m.missing_cached_detail)cell.textContent+=" + ?";row.append(cell);}const cost=element("td",money(m.estimated_cost_usd,true)+(m.unpriced_responses?" *":""));cost.title=m.unpriced_responses?t("partialLabel"):"USD";row.append(cost);$("model-rows").append(row);});
  $("empty").hidden=selected.models.length>0;
  $("empty-description").textContent=data.recorded_responses?t("emptyPeriod"):t("emptyHint");
  $("empty-setup").hidden=data.recorded_responses>0;
  $("response-count").textContent=totals.responses+" "+t("responses");
  $("export").disabled=!selected.models.length;
  renderChart(selected);renderRates();
  $("updated").textContent=t("updated")+" "+new Date(data.generated_at).toLocaleTimeString(lang,{hour:"2-digit",minute:"2-digit"})+" · "+data.timezone;
  $("config-snippet").textContent=`[otel]\nlog_user_prompt = false\nexporter = { otlp-http = { endpoint = "http://127.0.0.1:${data.port}/v1/logs", protocol = "json" } }`;
}
function renderChart(selected){
  const series=selected.series,max=Math.max(...series.map(s=>Number(s.estimated_cost_usd)||0),.01);
  $("chart-max").textContent=money(max);$("chart").replaceChildren();
  series.forEach(s=>{const slot=element("div",undefined,"bar-slot"),bar=element("div",undefined,"bar");
    const value=Number(s.estimated_cost_usd)||0;
    bar.classList.toggle("zero",value===0);bar.classList.toggle("future",s.future);bar.classList.toggle("unpriced",s.unpriced_responses>0);
    bar.style.height=(s.estimated_cost_usd===null?8:Math.max(1,value/max*100))+"%";
    const title=(period==="day"?s.label+":00":s.label)+" · "+(s.estimated_cost_usd===null?t("unknown"):money(s.estimated_cost_usd,true))+(s.unpriced_responses?" · "+t("partialLabel"):"");slot.title=title;slot.setAttribute("aria-label",title);slot.append(bar);$("chart").append(slot);
  });
  const labels=period==="day"?["00:00","06:00","12:00","18:00","23:00"]:[series[0].label,series[Math.floor(series.length/2)].label,series.at(-1).label].map(s=>s.slice(5));
  $("chart-labels").replaceChildren(...labels.map(label=>element("span",label)));
  $("chart-range").textContent=t(period)+" · "+selected.start.slice(0,10)+" · "+data.timezone;
}
function renderRates(){
  if(!data)return;$("rate-rows").replaceChildren();
  for(const [model,rates] of Object.entries(data.pricing.models)){
    const extended=$("long-context").checked&&rates.long_context;
    const tier=extended||rates,row=element("tr");
    row.append(modelCell(model,extended?"> "+number(extended.above_input_tokens)+" "+t("threshold"):t("standard")));
    for(const key of ["input","cached_input","output"])row.append(element("td",money(tier[key],true)));
    $("rate-rows").append(row);
  }
  $("rates-date").textContent=t("asOf")+": "+(data.pricing.as_of||"—")+" · "+t("rateUnit");
}
async function refresh(){
  if(loading)return;loading=true;$("refresh").disabled=true;
  try{const response=await fetch("/api/dashboard",{cache:"no-store",signal:AbortSignal.timeout(6000)});if(!response.ok)throw Error(response.status);data=await response.json();render();$("error").hidden=true;$("status-dot").classList.add("online");$("status-text").textContent=t("online");}
  catch{ $("error").textContent=t("error");$("error").hidden=false;$("status-dot").classList.remove("online");$("status-text").textContent=t("offline"); }
  finally{loading=false;$("refresh").disabled=false;}
}
function showSetup(){if(!$("config-snippet").textContent)$("config-snippet").textContent=`[otel]\nlog_user_prompt = false\nexporter = { otlp-http = { endpoint = "http://127.0.0.1:${location.port}/v1/logs", protocol = "json" } }`;$("setup-dialog").showModal();}
function toast(message){$("toast").textContent=message;$("toast").hidden=false;setTimeout(()=>$("toast").hidden=true,2500);}
$("language").onclick=()=>{lang=lang==="zh"?"en":"zh";localStorage.setItem("superlight.language",lang);native({action:"language",value:lang});translate();};
document.querySelectorAll("[data-period]").forEach(button=>button.onclick=()=>{period=button.dataset.period;document.querySelectorAll("[data-period]").forEach(b=>{b.classList.toggle("selected",b===button);b.setAttribute("aria-pressed",String(b===button));});if(data)render();});
document.querySelectorAll("[data-view]").forEach(button=>button.onclick=()=>{document.querySelectorAll("[data-view]").forEach(b=>{b.classList.toggle("active",b===button);b.setAttribute("aria-pressed",String(b===button));});$("overview-view").hidden=button.dataset.view!=="overview";$("rates-view").hidden=button.dataset.view!=="rates";});
$("setup").onclick=showSetup;$("empty-setup").onclick=showSetup;$("close-dialog").onclick=()=>$("setup-dialog").close();
$("setup-dialog").addEventListener("click",e=>{if(e.target===$("setup-dialog")){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
$("copy-config").onclick=async()=>{try{if(!native({action:"copyConfig"}))await navigator.clipboard.writeText($("config-snippet").textContent);toast(t("copied"));}catch{toast(t("copyFailed"));}};
$("settings").onclick=()=>{if(!native({action:"settings"}))showSetup();};
$("open-dashboard").onclick=()=>{if(!native({action:"openDashboard"}))window.open("/","_blank","noopener");};
$("refresh").onclick=refresh;$("long-context").onchange=renderRates;
$("export").onclick=()=>{if(!data)return;if(native({action:"export",period}))return;const blob=new Blob([JSON.stringify({currency:data.currency,period,price_as_of:data.pricing.as_of,...data.periods[period]},null,2)],{type:"application/json"});const url=URL.createObjectURL(blob),link=element("a");link.href=url;link.download=`superlight-${period}-${data.generated_at.slice(0,10)}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast(t("exported"));};
translate();refresh();setInterval(()=>{if(!document.hidden)refresh();},10000);document.addEventListener("visibilitychange",()=>{if(!document.hidden)refresh();});

window.addEventListener("storage",event=>{if(event.key==="superlight.language"&&words[event.newValue]){lang=event.newValue;translate();}});
