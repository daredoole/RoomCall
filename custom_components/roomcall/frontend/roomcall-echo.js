/* Live TECHO5 audio. Neither the browser nor Home Assistant records this stream. */
const WORKLET = `class Duplex extends AudioWorkletProcessor {
  constructor() {
    super(); this.capture=new Int16Array(640); this.used=0; this.phase=0;
    this.ring=new Float32Array(16384); this.head=0; this.count=0; this.playPhase=0;
    this.port.onmessage=({data})=>{
      const pcm=new Int16Array(data);
      for(const value of pcm) {
        if(this.count>=8000) {this.head=(this.head+1)%16384;this.count--;}
        this.ring[(this.head+this.count)%16384]=value/32768; this.count++;
      }
    };
  }
  process(inputs,outputs) {
    const input=inputs[0]?.[0];
    if(input) {
      while(this.phase<input.length) {
        const value=Math.max(-1,Math.min(1,input[Math.floor(this.phase)]));
        this.capture[this.used++]=Math.round(value*(value<0?32768:32767));
        this.phase+=sampleRate/16000;
        if(this.used===640) {
          this.port.postMessage(this.capture.buffer,[this.capture.buffer]);
          this.capture=new Int16Array(640);this.used=0;
        }
      }
      this.phase-=input.length;
    }
    const output=outputs[0]?.[0];
    if(output) for(let i=0;i<output.length;i++) {
      if(this.count<2) {output[i]=0;this.playPhase=0;continue;}
      const a=this.ring[this.head],b=this.ring[(this.head+1)%16384];
      output[i]=a+(b-a)*this.playPhase;
      this.playPhase+=16000/sampleRate;
      while(this.playPhase>=1&&this.count>1) {
        this.playPhase--;this.head=(this.head+1)%16384;this.count--;
      }
    }
    return true;
  }
} registerProcessor('roomcall-echo-duplex',Duplex);`;

export class EchoClient {
  constructor(controls) {
    this.controls=controls;this.accepted=false;this.inflight=0;
    this.panel=document.createElement('div');controls.root.append(this.panel);
  }
  async call(speaker,invitation) {
    try {
      this.context=new AudioContext();await this.context.resume();
      if(this.closed)return;
      this.stream=await navigator.mediaDevices.getUserMedia({audio:{
        echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
      if(this.closed){this.stream.getTracks().forEach(track=>track.stop());return;}
      const url=URL.createObjectURL(new Blob([WORKLET],{type:'text/javascript'}));
      try {await this.context.audioWorklet.addModule(url);}
      finally {URL.revokeObjectURL(url);}
      if(this.closed)return;
      this.node=new AudioWorkletNode(this.context,'roomcall-echo-duplex');
      this.context.createMediaStreamSource(this.stream).connect(this.node);
      this.node.connect(this.context.destination);
      this.node.port.onmessage=({data})=>{
        if(!this.accepted||this.inflight>=4)return;
        const pcm=btoa(String.fromCharCode(...new Uint8Array(data)));
        this.inflight++;
        this.controls.card._hass.callWS({type:'roomcall/echo_audio',call_id:this.id,pcm})
          .catch(error=>{this.controls.error(error);this.close();})
          .finally(()=>this.inflight--);
      };
      this.unsubscribe=await this.controls.card._hass.connection.subscribeMessage(
        event=>this.event(event),{type:'roomcall/echo_call',speaker,
          name:this.controls.t('phone'),...(invitation?{invitation}:{})});
      // A hang-up during async setup must also unsubscribe the late subscription.
      if(this.closed)this.unsubscribe();
    } catch(error) {await this.close();throw error;}
  }
  event(event) {
    if(this.closed)return;
    this.id=event.id;
    if(event.type==='audio') {
      const bytes=Uint8Array.from(atob(event.pcm),c=>c.charCodeAt(0));
      this.node?.port.postMessage(bytes.buffer,[bytes.buffer]);
    } else if(event.type==='accepted') {
      this.accepted=true;this.controls.result.textContent=this.controls.t('connected');
    } else if(event.type==='ended') {
      this.controls.result.textContent=this.controls.t(event.reason==='ended'?'ended':event.reason);
      this.close();
    } else {
      this.controls.result.textContent=this.controls.t('echo_ringing');
      this.panel.replaceChildren(this.controls.button('hangup',()=>{
        this.controls.result.textContent=this.controls.t('ended');this.close();
      }));
    }
  }
  async close() {
    if(this.closed)return;
    this.closed=true;this.accepted=false;
    this.node?.disconnect();
    this.stream?.getTracks().forEach(track=>track.stop());
    this.unsubscribe?.();this.panel.remove();
    await this.context?.close().catch(()=>{});
  }
}
