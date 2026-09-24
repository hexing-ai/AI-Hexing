export const shapeOptions = [
  {value:'orbit',label:'圆形',shape:'cercle'}, {value:'pixel',label:'圆角方形',shape:'squircle'},
  {value:'spark',label:'六边形',shape:'hexagone'}, {value:'pebble',label:'鹅卵石',shape:'galet'},
  {value:'capsule',label:'胶囊',shape:'capsule'}, {value:'triangle',label:'三角形',shape:'triangle'},
  {value:'cloud',label:'云朵',shape:'nuage'}, {value:'drop',label:'水滴',shape:'goutte'},
] as const;
export const colorOptions = [
  {value:'mint',label:'青绿',hex:'#2fbfa0'}, {value:'violet',label:'紫色',hex:'#8b5cf6'},
  {value:'amber',label:'琥珀',hex:'#f0b429'}, {value:'red',label:'珊瑚红',hex:'#e8483f'},
  {value:'orange',label:'橙色',hex:'#f08a24'}, {value:'green',label:'翠绿',hex:'#3ecf8e'},
  {value:'blue',label:'天蓝',hex:'#3b93f0'}, {value:'pink',label:'玫红',hex:'#e152b0'},
  {value:'brown',label:'棕色',hex:'#8b5e3c'}, {value:'gray',label:'银灰',hex:'#a3a3a3'},
  {value:'ink',label:'墨黑',hex:'#0a0a0c'}, {value:'cream',label:'奶白',hex:'#f1efe9'},
] as const;
export type Avatar={preset:typeof shapeOptions[number]['value'];palette:typeof colorOptions[number]['value']};
export const palette=Object.fromEntries(colorOptions.map(c=>[c.value,c.hex])) as Record<Avatar['palette'],string>;
