// Every visible string lives here so switching language rewrites the whole
// chrome, not just the video track. `brandLead`/`brandTail` mirror the two-part
// masthead: 盘面|十条 in Chinese, MARKET|TEN in English.
const COPY={
  cn:{locale:'zh-CN',docTitle:'盘面十条',brandLead:'盘面',brandTail:'十条',
      kicker:'每日财经短视频',empty:'今日节目正在编排',error:'暂时无法加载节目',
      catalog:'往期节目',count:n=>`${n} 期节目`,rating:'本期评分',ratingAria:'评分',
      like:'点赞',star:n=>`${n} 星`,
      liked:'你已点赞',rated:'你已评分',
      ratingStats:(average,votes)=>`${average} 分 · ${votes} 人评分`,
      noRating:'还没有人评分',
      fallbackTitle:'中美市场，十条说清',
      fallbackSummary:'精选当天最值得关注的市场变化，用一段视频完成盘前梳理。'},
  en:{locale:'en-US',docTitle:'Market Ten',brandLead:'MARKET',brandTail:'TEN',
      kicker:'DAILY MARKET BRIEFING',empty:"Today's episode is being produced",
      error:'Episodes are unavailable right now',
      catalog:'PAST EPISODES',count:n=>`${n} ${n===1?'EPISODE':'EPISODES'}`,
      rating:'Rate this episode',ratingAria:'Rating',
      like:'Like',star:n=>`${n} star${n===1?'':'s'}`,
      liked:'You already liked this',rated:'You already rated this',
      ratingStats:(average,votes)=>`${average} · ${votes} vote${votes===1?'':'s'}`,
      noRating:'No ratings yet',
      fallbackTitle:'China and the U.S., in ten moves',
      fallbackSummary:'The market moves that mattered today, condensed into a single briefing.'}
};
const state={episodes:[],current:null,reactions:null,language:'cn'};
const player=document.querySelector('#player');
const empty=document.querySelector('#empty-player');
const stars=document.querySelector('#star-buttons');

function copy(){return COPY[state.language];}
function trackFor(episode){
  // Fall back to the flat single-language fields so episodes published before
  // the bilingual pipeline still play instead of throwing on a missing key.
  const tracks=episode.languages;
  if(!tracks)return{playback_url:episode.playback_url,cover_url:episode.cover_url};
  return tracks[state.language]||tracks.cn||Object.values(tracks)[0];
}
function titleFor(episode){
  return (state.language==='en'&&episode.title_en)?episode.title_en:episode.title;
}
function storyTitle(story){
  return (state.language==='en'&&story.title_en)?story.title_en:story.title;
}
function renderChrome(){
  const text=copy();
  document.documentElement.lang=state.language==='en'?'en':'zh-CN';
  document.title=text.docTitle;
  document.querySelector('#brand-lead').textContent=text.brandLead;
  document.querySelector('#brand-tail').textContent=text.brandTail;
  document.querySelector('#kicker').textContent=text.kicker;
  document.querySelector('#today').textContent=
    new Intl.DateTimeFormat(text.locale,{dateStyle:'full'}).format(new Date());
  document.querySelector('#catalog-title').textContent=text.catalog;
  document.querySelector('#rating-legend').textContent=text.rating;
  document.querySelector('#stars').setAttribute('aria-label',text.ratingAria);
  [...stars.children].forEach((button,index)=>{
    const label=text.star(index+1);
    button.title=label;button.setAttribute('aria-label',label);
  });
  renderReactions();
  if(!state.current){
    empty.textContent=text.empty;
    document.querySelector('#episode-title').textContent=text.fallbackTitle;
    document.querySelector('#episode-summary').textContent=text.fallbackSummary;
  }
}

// The server owns the totals and the one-vote-per-address rule, so the UI never
// increments locally -- every render is driven by the summary it just returned.
function renderReactions(){
  const text=copy();
  const summary=state.reactions;
  const like=document.querySelector('#like');
  const stats=document.querySelector('#rating-stats');
  const mineStar=summary?summary.mine.star:0;
  const mineLike=Boolean(summary&&summary.mine.like);
  document.querySelector('#like-count').textContent=summary?summary.likes:0;
  like.disabled=mineLike;
  like.classList.toggle('voted',mineLike);
  like.title=mineLike?text.liked:text.like;
  like.setAttribute('aria-label',like.title);
  [...stars.children].forEach((button,index)=>{
    button.classList.toggle('active',index<mineStar);
    button.disabled=mineStar>0;
  });
  document.querySelector('#stars').title=mineStar>0?text.rated:'';
  if(!summary){stats.textContent='';return;}
  stats.textContent=summary.stars.votes
    ? text.ratingStats(summary.stars.average,summary.stars.votes)
    : text.noRating;
}

async function load(){
  try{
    const response=await fetch('/api/videos');
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    state.episodes=await response.json();
    renderCatalog();
    if(state.episodes.length)selectEpisode(state.episodes[0]);
  }catch(error){
    empty.textContent=copy().error;
    console.error(error);
  }
}
function renderCatalog(){
  const host=document.querySelector('#episodes');host.replaceChildren();
  document.querySelector('#episode-count').textContent=copy().count(state.episodes.length);
  const template=document.querySelector('#episode-template');
  state.episodes.forEach(episode=>{
    const node=template.content.cloneNode(true);const button=node.querySelector('button');
    const track=trackFor(episode);
    node.querySelector('img').src=track.cover_url;node.querySelector('img').alt=titleFor(episode);
    node.querySelector('b').textContent=titleFor(episode);node.querySelector('small').textContent=episode.run_date;
    button.addEventListener('click',()=>selectEpisode(episode));host.append(node);
  });
}
function selectEpisode(episode,resumeAt=0){
  state.current=episode;
  const track=trackFor(episode);
  player.pause();
  player.src=track.playback_url;player.poster=track.cover_url;player.load();
  if(resumeAt>0){
    player.addEventListener('loadedmetadata',()=>{player.currentTime=Math.min(resumeAt,player.duration||resumeAt);},{once:true});
  }
  empty.hidden=true;document.querySelector('#episode-title').textContent=titleFor(episode);
  document.querySelector('#episode-summary').textContent=episode.stories.map(storyTitle).slice(0,3).join(' · ');
  state.reactions=null;renderReactions();
  loadReactions(episode);
}
function switchLanguage(language){
  if(language===state.language)return;
  state.language=language;
  document.querySelectorAll('#lang-switch button').forEach(button=>{
    const active=button.dataset.lang===language;
    button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));
  });
  renderChrome();
  renderCatalog();
  if(state.current){
    // Keep the viewer where they were: the two renders share one timeline.
    const position=player.currentTime;const wasPlaying=!player.paused;
    selectEpisode(state.current,position);
    if(wasPlaying)player.play().catch(error=>console.error(error));
  }
}
async function react(kind,value=1){
  if(!state.current)return;
  const response=await fetch(`/api/videos/${reactionKey(state.current)}/reactions`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,value})});
  // 409 means this address already voted. The body still carries the live
  // totals, so render them rather than discarding a useful response.
  if(!response.ok&&response.status!==409)throw new Error(`Reaction failed: ${response.status}`);
  state.reactions=await response.json();
  renderReactions();
}
function reactionKey(episode){return episode.run_date.replaceAll('-','').slice(2);}
async function loadReactions(episode){
  try{
    const response=await fetch(`/api/videos/${reactionKey(episode)}/reactions`);
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const summary=await response.json();
    // A slow request for a previous episode must not overwrite the current one.
    if(state.current!==episode)return;
    state.reactions=summary;renderReactions();
  }catch(error){console.error(error);}
}
document.querySelector('#lang-switch').addEventListener('click',event=>{
  const button=event.target.closest('button[data-lang]');
  if(button)switchLanguage(button.dataset.lang);
});
document.querySelector('#like').addEventListener('click',async()=>{
  try{await react('like');}catch(error){console.error(error);}
});
for(let score=1;score<=5;score+=1){
  const button=document.createElement('button');button.type='button';button.textContent='★';
  button.addEventListener('click',async()=>{try{await react('star',score);}catch(error){console.error(error);}});
  stars.append(button);
}
// renderChrome() labels the stars, so it must run after they exist.
renderChrome();
load();
