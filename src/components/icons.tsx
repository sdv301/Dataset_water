import React, { type SVGProps } from 'react';

type Node = [string, Record<string, string>];

function renderNodes(nodes: Node[]) {
  return nodes.map(([tag, attrs], i) => {
    const { key, ...rest } = attrs;
    return React.createElement(tag, { key: key ?? String(i), ...rest });
  });
}

function createIcon(nodes: Node[]) {
  return function Icon({ className, ...props }: SVGProps<SVGSVGElement>) {
    return (
      <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden {...props}>
        {renderNodes(nodes)}
      </svg>
    );
  };
}

export const Activity = createIcon([["path",{"d":"M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2","key":"169zse"}]]);
export const Map = createIcon([["path",{"d":"M14.106 5.553a2 2 0 0 0 1.788 0l3.659-1.83A1 1 0 0 1 21 4.619v12.764a1 1 0 0 1-.553.894l-4.553 2.277a2 2 0 0 1-1.788 0l-4.212-2.106a2 2 0 0 0-1.788 0l-3.659 1.83A1 1 0 0 1 3 19.381V6.618a1 1 0 0 1 .553-.894l4.553-2.277a2 2 0 0 1 1.788 0z","key":"169xi5"}],["path",{"d":"M15 5.764v15","key":"1pn4in"}],["path",{"d":"M9 3.236v15","key":"1uimfh"}]]);
export const Calendar = createIcon([["path",{"d":"M8 2v4","key":"1cmpym"}],["path",{"d":"M16 2v4","key":"4m81vk"}],["rect",{"width":"18","height":"18","x":"3","y":"4","rx":"2","key":"1hopcy"}],["path",{"d":"M3 10h18","key":"8toen8"}]]);
export const LayoutDashboard = createIcon([["rect",{"width":"7","height":"9","x":"3","y":"3","rx":"1","key":"10lvy0"}],["rect",{"width":"7","height":"5","x":"14","y":"3","rx":"1","key":"16une8"}],["rect",{"width":"7","height":"9","x":"14","y":"12","rx":"1","key":"1hutg5"}],["rect",{"width":"7","height":"5","x":"3","y":"16","rx":"1","key":"ldoo1y"}]]);
export const Settings2 = createIcon([["path",{"d":"M14 17H5","key":"gfn3mx"}],["path",{"d":"M19 7h-9","key":"6i9tg"}],["circle",{"cx":"17","cy":"17","r":"3","key":"18b49y"}],["circle",{"cx":"7","cy":"7","r":"3","key":"dfmy0x"}]]);
export const Thermometer = createIcon([["path",{"d":"M14 4v10.54a4 4 0 1 1-4 0V4a2 2 0 0 1 4 0Z","key":"17jzev"}]]);
export const CloudRain = createIcon([["path",{"d":"M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242","key":"1pljnt"}],["path",{"d":"M16 14v6","key":"1j4efv"}],["path",{"d":"M8 14v6","key":"17c4r9"}],["path",{"d":"M12 16v6","key":"c8a4gj"}]]);
export const Snowflake = createIcon([["path",{"d":"m10 20-1.25-2.5L6 18","key":"18frcb"}],["path",{"d":"M10 4 8.75 6.5 6 6","key":"7mghy3"}],["path",{"d":"m14 20 1.25-2.5L18 18","key":"1chtki"}],["path",{"d":"m14 4 1.25 2.5L18 6","key":"1b4wsy"}],["path",{"d":"m17 21-3-6h-4","key":"15hhxa"}],["path",{"d":"m17 3-3 6 1.5 3","key":"11697g"}],["path",{"d":"M2 12h6.5L10 9","key":"kv9z4n"}],["path",{"d":"m20 10-1.5 2 1.5 2","key":"1swlpi"}],["path",{"d":"M22 12h-6.5L14 15","key":"1mxi28"}],["path",{"d":"m4 10 1.5 2L4 14","key":"k9enpj"}],["path",{"d":"m7 21 3-6-1.5-3","key":"j8hb9u"}],["path",{"d":"m7 3 3 6h4","key":"1otusx"}]]);
export const AlertOctagon = createIcon([["path",{"d":"M12 16h.01","key":"1drbdi"}],["path",{"d":"M12 8v4","key":"1got3b"}],["path",{"d":"M15.312 2a2 2 0 0 1 1.414.586l4.688 4.688A2 2 0 0 1 22 8.688v6.624a2 2 0 0 1-.586 1.414l-4.688 4.688a2 2 0 0 1-1.414.586H8.688a2 2 0 0 1-1.414-.586l-4.688-4.688A2 2 0 0 1 2 15.312V8.688a2 2 0 0 1 .586-1.414l4.688-4.688A2 2 0 0 1 8.688 2z","key":"1fd625"}]]);
export const TrendingUp = createIcon([["path",{"d":"M16 7h6v6","key":"box55l"}],["path",{"d":"m22 7-8.5 8.5-5-5L2 17","key":"1t1m79"}]]);
export const AlertTriangle = createIcon([["path",{"d":"m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3","key":"wmoenq"}],["path",{"d":"M12 9v4","key":"juzpu7"}],["path",{"d":"M12 17h.01","key":"p32p05"}]]);
export const Plus = createIcon([["path",{"d":"M5 12h14","key":"1ays0h"}],["path",{"d":"M12 5v14","key":"s699le"}]]);
export const X = createIcon([["path",{"d":"M18 6 6 18","key":"1bl5f8"}],["path",{"d":"m6 6 12 12","key":"d8bk6v"}]]);
export const BarChart2 = createIcon([["path",{"d":"M5 21v-6","key":"1hz6c0"}],["path",{"d":"M12 21V3","key":"1lcnhd"}],["path",{"d":"M19 21V9","key":"unv183"}]]);
export const Database = createIcon([["ellipse",{"cx":"12","cy":"5","rx":"9","ry":"3","key":"msslwz"}],["path",{"d":"M3 5V19A9 3 0 0 0 21 19V5","key":"1wlel7"}],["path",{"d":"M3 12A9 3 0 0 0 21 12","key":"mv7ke4"}]]);
export const Upload = createIcon([["path",{"d":"M12 3v12","key":"1x0j5s"}],["path",{"d":"m17 8-5-5-5 5","key":"7q97r8"}],["path",{"d":"M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4","key":"ih7n3h"}]]);
export const RefreshCw = createIcon([["path",{"d":"M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8","key":"v9h5vc"}],["path",{"d":"M21 3v5h-5","key":"1q7to0"}],["path",{"d":"M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16","key":"3uifl3"}],["path",{"d":"M8 16H3v5","key":"1cv678"}]]);
export const FileText = createIcon([["path",{"d":"M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z","key":"1rqfz7"}],["path",{"d":"M14 2v4a2 2 0 0 0 2 2h4","key":"tnqrlb"}],["path",{"d":"M10 9H8","key":"b1mrlr"}],["path",{"d":"M16 13H8","key":"t4e002"}],["path",{"d":"M16 17H8","key":"z1uh3a"}]]);
export const Loader2 = createIcon([["path",{"d":"M21 12a9 9 0 1 1-6.219-8.56","key":"13zald"}]]);
export const Crosshair = createIcon([["circle",{"cx":"12","cy":"12","r":"10","key":"1mglay"}],["line",{"x1":"22","x2":"18","y1":"12","y2":"12","key":"l9bcsi"}],["line",{"x1":"6","x2":"2","y1":"12","y2":"12","key":"13hhkx"}],["line",{"x1":"12","x2":"12","y1":"6","y2":"2","key":"10w3f3"}],["line",{"x1":"12","x2":"12","y1":"22","y2":"18","key":"15g9kq"}]]);
export const Search = createIcon([["path",{"d":"m21 21-4.34-4.34","key":"14j7rj"}],["circle",{"cx":"11","cy":"11","r":"8","key":"4ej97u"}]]);
export const ChevronDown = createIcon([["path",{"d":"m6 9 6 6 6-6","key":"qrunsl"}]]);
export const CheckCircle2 = createIcon([["circle",{"cx":"12","cy":"12","r":"10","key":"1mglay"}],["path",{"d":"m9 12 2 2 4-4","key":"dzmm74"}]]);
export const Circle = createIcon([["circle",{"cx":"12","cy":"12","r":"10","key":"1mglay"}]]);
export const AlertCircle = createIcon([["circle",{"cx":"12","cy":"12","r":"10","key":"1mglay"}],["line",{"x1":"12","x2":"12","y1":"8","y2":"12","key":"1pkeuh"}],["line",{"x1":"12","x2":"12.01","y1":"16","y2":"16","key":"4dfq90"}]]);
export const Info = createIcon([["circle",{"cx":"12","cy":"12","r":"10","key":"1mglay"}],["path",{"d":"M12 16v-4","key":"1dtifu"}],["path",{"d":"M12 8h.01","key":"e9boi3"}]]);
export const ZoomIn = createIcon([["circle",{"cx":"11","cy":"11","r":"8","key":"4ej97u"}],["line",{"x1":"21","x2":"16.65","y1":"21","y2":"16.65","key":"13gj7c"}],["line",{"x1":"11","x2":"11","y1":"8","y2":"14","key":"1vmskp"}],["line",{"x1":"8","x2":"14","y1":"11","y2":"11","key":"durymu"}]]);