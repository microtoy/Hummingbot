import{H as u,O as p,P as y,c as i,o as s,G as f,l as c,J as b,K as r,L as m,b as l,t as v,aH as h}from"./index-CbyJdw3w.js";var k=`
    .p-tag {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        background: dt('tag.primary.background');
        color: dt('tag.primary.color');
        font-size: dt('tag.font.size');
        font-weight: dt('tag.font.weight');
        padding: dt('tag.padding');
        border-radius: dt('tag.border.radius');
        gap: dt('tag.gap');
    }

    .p-tag-icon {
        font-size: dt('tag.icon.size');
        width: dt('tag.icon.size');
        height: dt('tag.icon.size');
    }

    .p-tag-rounded {
        border-radius: dt('tag.rounded.border.radius');
    }

    .p-tag-success {
        background: dt('tag.success.background');
        color: dt('tag.success.color');
    }

    .p-tag-info {
        background: dt('tag.info.background');
        color: dt('tag.info.color');
    }

    .p-tag-warn {
        background: dt('tag.warn.background');
        color: dt('tag.warn.color');
    }

    .p-tag-danger {
        background: dt('tag.danger.background');
        color: dt('tag.danger.color');
    }

    .p-tag-secondary {
        background: dt('tag.secondary.background');
        color: dt('tag.secondary.color');
    }

    .p-tag-contrast {
        background: dt('tag.contrast.background');
        color: dt('tag.contrast.color');
    }
`,$={root:function(t){var e=t.props;return["p-tag p-component",{"p-tag-info":e.severity==="info","p-tag-success":e.severity==="success","p-tag-warn":e.severity==="warn","p-tag-danger":e.severity==="danger","p-tag-secondary":e.severity==="secondary","p-tag-contrast":e.severity==="contrast","p-tag-rounded":e.rounded}]},icon:"p-tag-icon",label:"p-tag-label"},w=u.extend({name:"tag",style:k,classes:$}),C={name:"BaseTag",extends:p,props:{value:null,severity:null,rounded:Boolean,icon:String},style:w,provide:function(){return{$pcTag:this,$parentInstance:this}}};function o(n){"@babel/helpers - typeof";return o=typeof Symbol=="function"&&typeof Symbol.iterator=="symbol"?function(t){return typeof t}:function(t){return t&&typeof Symbol=="function"&&t.constructor===Symbol&&t!==Symbol.prototype?"symbol":typeof t},o(n)}function S(n,t,e){return(t=P(t))in n?Object.defineProperty(n,t,{value:e,enumerable:!0,configurable:!0,writable:!0}):n[t]=e,n}function P(n){var t=B(n,"string");return o(t)=="symbol"?t:t+""}function B(n,t){if(o(n)!="object"||!n)return n;var e=n[Symbol.toPrimitive];if(e!==void 0){var a=e.call(n,t);if(o(a)!="object")return a;throw new TypeError("@@toPrimitive must return a primitive value.")}return(t==="string"?String:Number)(n)}var z={name:"Tag",extends:C,inheritAttrs:!1,computed:{dataP:function(){return y(S({rounded:this.rounded},this.severity,this.severity))}}},L=["data-p"];function T(n,t,e,a,g,d){return s(),i("span",r({class:n.cx("root"),"data-p":d.dataP},n.ptmi("root")),[n.$slots.icon?(s(),f(m(n.$slots.icon),r({key:0,class:n.cx("icon")},n.ptm("icon")),null,16,["class"])):n.icon?(s(),i("span",r({key:1,class:[n.cx("icon"),n.icon]},n.ptm("icon")),null,16)):c("",!0),n.value!=null||n.$slots.default?b(n.$slots,"default",{key:2},function(){return[l("span",r({class:n.cx("label")},n.ptm("label")),v(n.value),17)]}):c("",!0)],16,L)}z.render=T;var j={name:"AngleRightIcon",extends:h};function N(n,t,e,a,g,d){return s(),i("svg",r({width:"14",height:"14",viewBox:"0 0 14 14",fill:"none",xmlns:"http://www.w3.org/2000/svg"},n.pti()),t[0]||(t[0]=[l("path",{d:"M5.25 11.1728C5.14929 11.1694 5.05033 11.1455 4.9592 11.1025C4.86806 11.0595 4.78666 10.9984 4.72 10.9228C4.57955 10.7822 4.50066 10.5916 4.50066 10.3928C4.50066 10.1941 4.57955 10.0035 4.72 9.86283L7.72 6.86283L4.72 3.86283C4.66067 3.71882 4.64765 3.55991 4.68275 3.40816C4.71785 3.25642 4.79932 3.11936 4.91585 3.01602C5.03238 2.91268 5.17819 2.84819 5.33305 2.83149C5.4879 2.81479 5.64411 2.84671 5.78 2.92283L9.28 6.42283C9.42045 6.56346 9.49934 6.75408 9.49934 6.95283C9.49934 7.15158 9.42045 7.34221 9.28 7.48283L5.78 10.9228C5.71333 10.9984 5.63193 11.0595 5.5408 11.1025C5.44966 11.1455 5.35071 11.1694 5.25 11.1728Z",fill:"currentColor"},null,-1)]),16)}j.render=N;export{z as a,j as s};
