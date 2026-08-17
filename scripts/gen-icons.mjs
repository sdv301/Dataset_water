import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');

const icons = {
  Activity: 'activity',
  Map: 'map',
  Calendar: 'calendar',
  LayoutDashboard: 'layout-dashboard',
  Settings2: 'settings-2',
  Thermometer: 'thermometer',
  CloudRain: 'cloud-rain',
  Snowflake: 'snowflake',
  AlertOctagon: 'octagon-alert',
  TrendingUp: 'trending-up',
  AlertTriangle: 'triangle-alert',
  Plus: 'plus',
  X: 'x',
  BarChart2: 'chart-no-axes-column',
  Database: 'database',
  Upload: 'upload',
  RefreshCw: 'refresh-cw',
  FileText: 'file-text',
  Loader2: 'loader-circle',
  Crosshair: 'crosshair',
  Search: 'search',
  ChevronDown: 'chevron-down',
  CheckCircle2: 'circle-check',
  Circle: 'circle',
  AlertCircle: 'circle-alert',
  Info: 'info',
  ZoomIn: 'zoom-in',
};

const lines = [];
lines.push("import type { SVGProps, ElementType } from 'react';");
lines.push('');
lines.push('type Node = [string, Record<string, string>];');
lines.push('');
lines.push('function renderNodes(nodes: Node[]) {');
lines.push('  return nodes.map(([tag, attrs], i) => {');
lines.push('    const { key, ...rest } = attrs;');
lines.push('    const Tag = tag as ElementType;');
lines.push('    return <Tag key={key ?? String(i)} {...rest} />;');
lines.push('  });');
lines.push('}');
lines.push('');
lines.push('function createIcon(nodes: Node[]) {');
lines.push('  return function Icon({ className, ...props }: SVGProps<SVGSVGElement>) {');
lines.push('    return (');
lines.push(
  '      <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden {...props}>',
);
lines.push('        {renderNodes(nodes)}');
lines.push('      </svg>');
lines.push('    );');
lines.push('  };');
lines.push('}');
lines.push('');

for (const [exportName, file] of Object.entries(icons)) {
  const mod = await import(`lucide-react/dist/esm/icons/${file}.js`);
  const nodes = mod.__iconNode;
  lines.push(`export const ${exportName} = createIcon(${JSON.stringify(nodes)});`);
}

const out = path.join(root, 'src', 'components', 'icons.tsx');
fs.writeFileSync(out, lines.join('\n'));
console.log('written', out);
