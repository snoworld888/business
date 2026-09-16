import ReactECharts from 'echarts-for-react'

interface Datum {
  name: string
  value: number
}

interface Props {
  data: Datum[]
  max?: number
  height?: number
  name?: string
  areaColor?: string
}

/** 通用雷达图：能力画像（5 维）与赛道评分（7 维）复用 */
export default function Radar({ data, max = 5, height = 320, name = '评分', areaColor }: Props) {
  const option = {
    tooltip: { trigger: 'item' },
    radar: {
      indicator: data.map((d) => ({ name: d.name, max })),
      radius: '66%',
      axisName: { color: '#64748B', fontSize: 12 },
      splitLine: { lineStyle: { color: '#E2E8F0' } },
      splitArea: {
        areaStyle: { color: ['#FFFFFF', '#F8FAFC'] },
      },
      axisLine: { lineStyle: { color: '#E2E8F0' } },
    },
    series: [
      {
        type: 'radar',
        name,
        symbolSize: 5,
        data: [
          {
            value: data.map((d) => d.value),
            name,
            areaStyle: { color: areaColor ?? 'rgba(37, 99, 235, 0.22)' },
            lineStyle: { color: '#2563EB', width: 2 },
            itemStyle: { color: '#2563EB' },
          },
        ],
      },
    ],
  }

  if (data.length === 0) {
    return <div className="tc-empty">暂无数据</div>
  }

  return (
    <ReactECharts
      option={option}
      style={{ height, width: '100%' }}
      opts={{ renderer: 'svg' }}
      notMerge
    />
  )
}
