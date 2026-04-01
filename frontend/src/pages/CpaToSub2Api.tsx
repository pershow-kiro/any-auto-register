import { useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  InputNumber,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  DeleteOutlined,
  DownloadOutlined,
  ReloadOutlined,
  SwapOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import { apiFetch } from '@/lib/utils'

const { Paragraph, Text } = Typography

type SourceFile = {
  name: string
  content: string
  size: number
}

type FileSummary = {
  name: string
  records: number
  success: number
  skipped: number
  failed: number
}

type ConvertError = {
  source: string
  index?: number
  message: string
}

type ConvertedItem = {
  source: string
  index: number
  email: string
  account_id: string
  organization_id: string
  client_id: string
  expires_at: number
}

type SkippedItem = {
  source: string
  index: number
  email: string
  quota_field: string
  quota_value: string
  message: string
}

type ConvertResult = {
  total_files: number
  total_records: number
  success_count: number
  skipped_count: number
  failed_count: number
  file_summaries: FileSummary[]
  skipped_items: SkippedItem[]
  errors: ConvertError[]
  payload: Record<string, unknown> | null
  items: ConvertedItem[]
  download_name: string
  upload_attempted?: boolean
  upload_success?: boolean
  upload_message?: string
  upload_logs?: string[]
}

function formatFileSize(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(2)} MB`
}

function formatExpiry(timestamp: number) {
  if (!timestamp) return '-'
  const date = new Date(timestamp * 1000)
  return Number.isNaN(date.getTime()) ? String(timestamp) : date.toLocaleString()
}

export default function CpaToSub2Api() {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [sourceFiles, setSourceFiles] = useState<SourceFile[]>([])
  const [concurrency, setConcurrency] = useState(3)
  const [priority, setPriority] = useState(50)
  const [reading, setReading] = useState(false)
  const [converting, setConverting] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState<ConvertResult | null>(null)

  const handleSelectFiles = () => {
    fileInputRef.current?.click()
  }

  const handleFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const fileList = Array.from(event.target.files || [])
    if (!fileList.length) return

    setReading(true)
    try {
      const files = await Promise.all(
        fileList.map(async (file) => ({
          name: file.name,
          content: await file.text(),
          size: file.size,
        }))
      )
      setSourceFiles(files)
      setResult(null)
      message.success(`已加载 ${files.length} 个文件`)
    } catch (e: any) {
      message.error(e?.message ? String(e.message) : '读取文件失败')
    } finally {
      setReading(false)
      event.target.value = ''
    }
  }

  const handleConvert = async () => {
    if (!sourceFiles.length) {
      message.warning('请先选择 CPA 账号文件')
      return
    }

    setConverting(true)
    try {
      const data = await apiFetch('/tools/cpa-to-sub2api', {
        method: 'POST',
        body: JSON.stringify({
          files: sourceFiles.map(({ name, content }) => ({ name, content })),
          concurrency,
          priority,
        }),
      })
      setResult(data)
      const summary = `转换完成：成功 ${data.success_count} 条，跳过 ${data.skipped_count || 0} 条，失败 ${data.failed_count} 条`
      if (data.success_count > 0) {
        message.success(summary)
      } else if ((data.skipped_count || 0) > 0 || data.failed_count > 0) {
        message.warning(summary)
      } else {
        message.warning('没有可导出的 Sub2API 账号，请检查 CPA 文件格式')
      }
    } catch (e: any) {
      message.error(e?.message ? String(e.message) : '转换失败')
    } finally {
      setConverting(false)
    }
  }

  const handleDirectUpload = async () => {
    if (!sourceFiles.length) {
      message.warning('请先选择 CPA 账号文件')
      return
    }

    setUploading(true)
    try {
      const data = await apiFetch('/tools/cpa-to-sub2api-upload', {
        method: 'POST',
        body: JSON.stringify({
          files: sourceFiles.map(({ name, content }) => ({ name, content })),
          concurrency,
          priority,
        }),
      })
      setResult(data)

      const summary = `上传完成：成功 ${data.success_count} 条，跳过 ${data.skipped_count || 0} 条，失败 ${data.failed_count} 条`
      if (data.upload_attempted && data.upload_success) {
        message.success(data.upload_message ? `${summary}，${data.upload_message}` : summary)
      } else if (data.upload_attempted) {
        message.error(data.upload_message ? `${summary}，${data.upload_message}` : summary)
      } else {
        message.warning(data.upload_message || summary)
      }
    } catch (e: any) {
      message.error(e?.message ? String(e.message) : '上传失败')
    } finally {
      setUploading(false)
    }
  }

  const handleExport = () => {
    if (!result?.payload) {
      message.warning('当前没有可导出的 Sub2API 数据')
      return
    }

    const blob = new Blob([JSON.stringify(result.payload, null, 2)], {
      type: 'application/json;charset=utf-8',
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = result.download_name || 'sub2api-data.json'
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    message.success('已开始导出 Sub2API 文件')
  }

  const handleReset = () => {
    setSourceFiles([])
    setResult(null)
  }

  return (
    <div style={{ padding: 0 }}>
      <input
        ref={fileInputRef}
        type="file"
        accept=".json,.txt,application/json,text/plain"
        multiple
        style={{ display: 'none' }}
        onChange={handleFileChange}
      />

      <div style={{ marginBottom: 24, display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 'bold', margin: 0 }}>CPA 转 Sub2API</h1>
          <p style={{ color: '#7a8ba3', marginTop: 4, marginBottom: 0 }}>
            上传 CPA 格式账号文件，解析后导出成 Sub2API 的 `sub2api-data` JSON
          </p>
        </div>
        <Space wrap>
          <Button icon={<ReloadOutlined />} onClick={handleReset} disabled={!sourceFiles.length && !result}>
            清空
          </Button>
          <Button
            type="primary"
            ghost
            icon={<UploadOutlined />}
            onClick={handleDirectUpload}
            loading={uploading}
            disabled={!sourceFiles.length}
          >
            直接上传 Sub2API
          </Button>
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            onClick={handleExport}
            disabled={!result?.payload}
          >
            导出 Sub2API
          </Button>
        </Space>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={10}>
          <Card title="上传与转换" styles={{ body: { display: 'flex', flexDirection: 'column', gap: 16 } }}>
            <Alert
              type="info"
              showIcon
              message="支持单个 CPA JSON、JSON 数组、JSON Lines"
              description="每条记录至少需要 access_token；email 会优先读取文件字段，没有时会尝试从 token 中解析。若识别到额度字段且额度为 0，会自动跳过，不导出也不上传到 Sub2API。直接上传会使用设置页里已配置的 Sub2API 参数。"
            />

            <div
              style={{
                border: '1px dashed rgba(120, 138, 163, 0.45)',
                borderRadius: 12,
                padding: 24,
                textAlign: 'center',
                background: 'rgba(120, 138, 163, 0.06)',
              }}
            >
              <Space direction="vertical" size={12}>
                <Button icon={<UploadOutlined />} onClick={handleSelectFiles} loading={reading}>
                  选择 CPA 文件
                </Button>
                <Text type="secondary">可一次选择多个 `.json` / `.txt` 文件</Text>
              </Space>
            </div>

            <Row gutter={12}>
              <Col span={12}>
                <Text type="secondary">并发</Text>
                <InputNumber
                  min={1}
                  value={concurrency}
                  onChange={(value) => setConcurrency(typeof value === 'number' ? value : 3)}
                  style={{ width: '100%', marginTop: 8 }}
                />
              </Col>
              <Col span={12}>
                <Text type="secondary">优先级</Text>
                <InputNumber
                  value={priority}
                  onChange={(value) => setPriority(typeof value === 'number' ? value : 50)}
                  style={{ width: '100%', marginTop: 8 }}
                />
              </Col>
            </Row>

            <Space wrap>
              <Button
                type="primary"
                icon={<SwapOutlined />}
                onClick={handleConvert}
                loading={converting}
                disabled={!sourceFiles.length}
              >
                开始转换
              </Button>
              <Button
                icon={<UploadOutlined />}
                onClick={handleDirectUpload}
                loading={uploading}
                disabled={!sourceFiles.length}
              >
                直接上传 Sub2API
              </Button>
            </Space>

            {sourceFiles.length > 0 ? (
              <Table<SourceFile>
                rowKey="name"
                size="small"
                pagination={false}
                dataSource={sourceFiles}
                columns={[
                  {
                    title: '文件',
                    dataIndex: 'name',
                    key: 'name',
                    ellipsis: true,
                  },
                  {
                    title: '大小',
                    key: 'size',
                    width: 110,
                    render: (_, record) => formatFileSize(record.size),
                  },
                ]}
              />
            ) : (
              <Empty description="还没有选择文件" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>
        </Col>

        <Col xs={24} xl={14}>
          <Card
            title="转换结果"
            extra={
              result?.upload_attempted
                ? result.upload_success
                  ? <Tag color="success">已上传</Tag>
                  : <Tag color="error">上传失败</Tag>
                : result?.payload
                  ? <Tag color="success">可导出</Tag>
                  : result?.skipped_count
                    ? <Tag color="warning">含跳过项</Tag>
                    : null
            }
          >
            {result ? (
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                {result.upload_attempted || result.upload_message ? (
                  <Alert
                    type={result.upload_attempted ? (result.upload_success ? 'success' : 'error') : 'warning'}
                    showIcon
                    message={
                      result.upload_attempted
                        ? result.upload_success
                          ? '已直接上传到 Sub2API'
                          : '上传到 Sub2API 失败'
                        : '未执行上传'
                    }
                    description={result.upload_message || '当前仅完成转换，尚未执行上传。'}
                  />
                ) : null}

                {result.upload_logs && result.upload_logs.length > 0 ? (
                  <Card size="small" title="上传日志">
                    <div
                      style={{
                        maxHeight: 240,
                        overflow: 'auto',
                        fontFamily: 'Consolas, Monaco, monospace',
                        fontSize: 12,
                        lineHeight: 1.7,
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                      }}
                    >
                      {result.upload_logs.map((line, index) => (
                        <div key={`${index}-${line}`}>{line}</div>
                      ))}
                    </div>
                  </Card>
                ) : null}

                <Row gutter={[16, 16]}>
                  <Col xs={12} sm={6}>
                    <Card size="small">
                      <Statistic title="成功" value={result.success_count} valueStyle={{ color: '#10b981' }} />
                    </Card>
                  </Col>
                  <Col xs={12} sm={6}>
                    <Card size="small">
                      <Statistic title="跳过" value={result.skipped_count} valueStyle={{ color: '#f59e0b' }} />
                    </Card>
                  </Col>
                  <Col xs={12} sm={6}>
                    <Card size="small">
                      <Statistic title="失败" value={result.failed_count} valueStyle={{ color: '#ef4444' }} />
                    </Card>
                  </Col>
                  <Col xs={12} sm={6}>
                    <Card size="small">
                      <Statistic title="总记录" value={result.total_records} />
                    </Card>
                  </Col>
                </Row>

                <Table<FileSummary>
                  rowKey="name"
                  size="small"
                  pagination={false}
                  dataSource={result.file_summaries}
                  columns={[
                    {
                      title: '文件',
                      dataIndex: 'name',
                      key: 'name',
                      ellipsis: true,
                    },
                    {
                      title: '记录数',
                      dataIndex: 'records',
                      key: 'records',
                      width: 88,
                    },
                    {
                      title: '成功',
                      dataIndex: 'success',
                      key: 'success',
                      width: 88,
                    },
                    {
                      title: '跳过',
                      dataIndex: 'skipped',
                      key: 'skipped',
                      width: 88,
                    },
                    {
                      title: '失败',
                      dataIndex: 'failed',
                      key: 'failed',
                      width: 88,
                    },
                  ]}
                />

                {result.skipped_items.length > 0 ? (
                  <Table<SkippedItem>
                    rowKey={(record) => `${record.source}-${record.index}-${record.quota_field}`}
                    size="small"
                    pagination={{ pageSize: 6, showSizeChanger: false }}
                    dataSource={result.skipped_items}
                    columns={[
                      {
                        title: '来源',
                        key: 'source',
                        render: (_, record) => (
                          <span>
                            {record.source} #{record.index}
                          </span>
                        ),
                      },
                      {
                        title: '邮箱',
                        dataIndex: 'email',
                        key: 'email',
                        ellipsis: true,
                        render: (value: string) => value || '-',
                      },
                      {
                        title: '额度字段',
                        dataIndex: 'quota_field',
                        key: 'quota_field',
                        ellipsis: true,
                        responsive: ['lg'],
                      },
                      {
                        title: '额度值',
                        dataIndex: 'quota_value',
                        key: 'quota_value',
                        width: 90,
                      },
                      {
                        title: '说明',
                        dataIndex: 'message',
                        key: 'message',
                      },
                    ]}
                  />
                ) : null}

                <Table<ConvertedItem>
                  rowKey={(record) => `${record.source}-${record.index}-${record.email}`}
                  size="small"
                  pagination={{ pageSize: 8, showSizeChanger: false }}
                  dataSource={result.items}
                  columns={[
                    {
                      title: '邮箱',
                      dataIndex: 'email',
                      key: 'email',
                      ellipsis: true,
                    },
                    {
                      title: 'Account ID',
                      dataIndex: 'account_id',
                      key: 'account_id',
                      ellipsis: true,
                    },
                    {
                      title: 'Organization',
                      dataIndex: 'organization_id',
                      key: 'organization_id',
                      ellipsis: true,
                      responsive: ['lg'],
                    },
                    {
                      title: '过期时间',
                      dataIndex: 'expires_at',
                      key: 'expires_at',
                      width: 180,
                      render: (value: number) => formatExpiry(value),
                    },
                  ]}
                />

                {result.errors.length > 0 ? (
                  <Table<ConvertError>
                    rowKey={(record, index) => `${record.source}-${record.index || 0}-${index}`}
                    size="small"
                    pagination={{ pageSize: 6, showSizeChanger: false }}
                    dataSource={result.errors}
                    columns={[
                      {
                        title: '来源',
                        key: 'source',
                        render: (_, record) => (
                          <span>
                            {record.source}
                            {record.index ? ` #${record.index}` : ''}
                          </span>
                        ),
                      },
                      {
                        title: '错误原因',
                        dataIndex: 'message',
                        key: 'message',
                      },
                    ]}
                  />
                ) : (
                  <Alert type="success" showIcon message="所有已解析记录都已成功转换" />
                )}
              </Space>
            ) : (
              <Empty description="选择文件后开始转换" image={Empty.PRESENTED_IMAGE_SIMPLE}>
                <Paragraph type="secondary" style={{ marginBottom: 0 }}>
                  导出结果会保持为 Sub2API 原生 `sub2api-data` 结构，可直接用于后续导入。
                </Paragraph>
              </Empty>
            )}
          </Card>
        </Col>
      </Row>

      <Card style={{ marginTop: 16 }}>
        <Paragraph style={{ marginBottom: 8 }}>
          <Text strong>说明：</Text>
          这个页面只做格式转换，不会把上传的 CPA 账号写入当前系统账号库。
        </Paragraph>
        <Paragraph style={{ marginBottom: 0 }}>
          导出文件会包含 `data.type = sub2api-data`、`accounts`、`proxies` 等字段，和系统现有 Sub2API 批量上传逻辑保持一致；如果 CPA 记录额度为 0，则该账号会自动跳过，不写入导出文件，也不会参与直接上传。
        </Paragraph>
      </Card>

      <Button
        icon={<DeleteOutlined />}
        onClick={handleReset}
        disabled={!sourceFiles.length && !result}
        style={{ marginTop: 16 }}
      >
        重置页面状态
      </Button>
    </div>
  )
}
