import { useCallback, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router-dom';
import {
  useImages,
  useImageBuilds,
  useImportImage,
  useUpdateImage,
  useCreateBuild,
  type ImportImageBody,
  type CreateBuildBody,
} from '@/api/hooks/useImages';
import { useProjects } from '@/api/hooks/useGroups';
import { Table, TableToolbar, sortAccessor, type Column } from '@/components/Table';
import { EmptyState, NoResults, TableSkeleton } from '@/components/EmptyState';
import { CopyButton } from '@/components/CopyButton';
import { Timestamp } from '@/components/Timestamp';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { PageHeader, BackLink } from '@/components/PageHeader';
import { DisabledReason } from '@/components/Field';
import { useFormGuard } from '@/hooks/useUnsavedGuard';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';

// The image and template registry plus image builds (/images, /images/import, /image-builds).

interface ImageRow {
  id: string;
  name: string;
  registry: string;
  kind: string;
  import_status?: string;
  tags?: Record<string, string>;
  cuda_version?: string | null;
  supported_gpus?: string[];
  public?: boolean;
  created_at?: string;
}

// Tag keys that get their own column, and are therefore hidden from the generic tag list.
const _RESERVED_TAG_KEYS = new Set(['cuda_version', 'supported_gpus']);
interface BuildRow {
  id: string;
  group_id: string;
  name: string;
  source: string;
  status: string;
  image_ref?: string | null;
  created_at?: string;
  finished_at?: string | null;
}

const KIND_PILL: Record<string, string> = {
  image: 'bg-primary-soft text-primary',
  template: 'bg-free-soft text-free',
  iso: 'bg-warn-soft text-warn',
};

const BUILD_PILL: Record<string, string> = {
  queued: 'bg-warn-soft text-warn',
  building: 'bg-warn-soft text-warn',
  pushing: 'bg-warn-soft text-warn',
  scanning: 'bg-warn-soft text-warn',
  succeeded: 'bg-free-soft text-free',
  failed: 'bg-danger-soft text-danger',
  cancelled: 'bg-danger-soft text-danger',
};

export function AdminImages() {
  const { t } = useTranslation();
  // Tab, kind and search in the URL, so a catalogue view is shareable and survives Back.
  const table = useTableState('', { sort: 'name', dir: 'asc', tab: 'images' });
  const tab = (table.tab ?? 'images') as 'images' | 'builds';
  const setTab = (v: 'images' | 'builds') => table.setTab(v);
  const [kind, setKind] = useState<'' | 'image' | 'template' | 'iso'>('');
  const q = table.query;

  const imagesQ = useImages({ kind: kind || undefined, q: q.trim() || undefined });
  const buildsQ = useImageBuilds({});
  const update = useUpdateImage();
  const pushToast = useUiStore((s) => s.pushToast);

  const images = useMemo(() => (imagesQ.data?.data ?? []) as ImageRow[], [imagesQ.data]);
  const builds = useMemo(() => (buildsQ.data?.data ?? []) as BuildRow[], [buildsQ.data]);

  const togglePublic = useCallback(
    (r: ImageRow) =>
      update.mutate(
        { id: r.id, public: !(r.public ?? true) },
        {
          onSuccess: () => pushToast('success', t('admin.images.visibilityChanged', { name: r.name, visibility: t(r.public ?? true ? 'admin.images.private' : 'admin.images.public') })),
          onError: (e) => pushToast('error', humanizeError(asApiError(e))),
        },
      ),
    [update, pushToast, t],
  );

  const imageColumns: Column<ImageRow>[] = useMemo(
    () => [
      {
        key: 'name',
        header: t('common.name'),
        render: (r) => (
          <div>
            <b>{r.name}</b>
            <div className="text-muted text-[12px] font-mono">{r.registry}</div>
          </div>
        ),
      },
      {
        key: 'kind',
        header: t('admin.images.colKind'),
        render: (r) => (
          <span className={`gs-pill ${KIND_PILL[r.kind] ?? 'bg-surface-2 text-muted'}`}>{r.kind}</span>
        ),
      },
      {
        key: 'cuda',
        header: 'CUDA',
        render: (r) =>
          r.cuda_version ? (
            <span className="gs-pill bg-primary-soft text-primary font-mono">{r.cuda_version}</span>
          ) : (
            <span className="text-muted">—</span>
          ),
      },
      {
        key: 'supported_gpus',
        header: t('admin.images.colSupportedGpus'),
        render: (r) =>
          r.supported_gpus?.length ? (
            <div className="flex flex-wrap gap-1">
              {r.supported_gpus.map((g) => (
                <span key={g} className="gs-pill bg-surface-2 text-muted">{g}</span>
              ))}
            </div>
          ) : (
            <span className="text-muted">{t('admin.images.everyGpu')}</span>
          ),
      },
      {
        key: 'tags',
        header: t('admin.images.colTags'),
        render: (r) => {
          const entries = Object.entries(r.tags ?? {}).filter(([k]) => !_RESERVED_TAG_KEYS.has(k));
          if (entries.length === 0) return <span className="text-muted">—</span>;
          return (
            <div className="flex flex-wrap gap-1">
              {entries.map(([k, v]) => (
                <span key={k} className="gs-pill bg-surface-2 text-muted font-mono">
                  {k}:{String(v)}
                </span>
              ))}
            </div>
          );
        },
      },
      {
        key: 'import_status',
        header: t('common.status'),
        render: (r) =>
          r.import_status ? (
            <span
              className={`gs-pill ${
                r.import_status === 'ready'
                  ? 'bg-free-soft text-free'
                  : r.import_status === 'failed'
                    ? 'bg-danger-soft text-danger'
                    : 'bg-warn-soft text-warn'
              }`}
            >
              {r.import_status}
            </span>
          ) : (
            <span className="gs-pill bg-free-soft text-free">ready</span>
          ),
      },
      {
        key: 'public',
        header: t('admin.images.colPublic'),
        render: (r) => {
          const isPublic = r.public ?? true;
          return (
            <button
              type="button"
              className={`gs-pill cursor-pointer whitespace-nowrap ${isPublic ? 'bg-free-soft text-free' : 'bg-surface-2 text-muted'}`}
              disabled={update.isPending}
              onClick={() => togglePublic(r)}
              title={isPublic ? t('admin.images.makePrivateHint') : t('admin.images.makePublicHint')}
            >
              {isPublic ? t('admin.images.public') : t('admin.images.private')}
            </button>
          );
        },
      },
    ],
    // `t` is a dependency so the headers re-render when the language changes.
    [update.isPending, togglePublic, t],
  );

  const buildColumns: Column<BuildRow>[] = useMemo(
    () => [
      {
        key: 'name',
        header: t('admin.images.colBuild'),
        sortBy: (r) => r.name,
        render: (r) => (
          <div className="min-w-0">
            <b>{r.name}</b>
            <div className="flex items-center gap-1 text-muted text-[12px]">
              <code className="font-mono truncate max-w-[150px]" title={r.id}>{r.id}</code>
              <CopyButton value={r.id} label={t('admin.images.copyBuildId')} />
            </div>
          </div>
        ),
      },
      { key: 'source', header: t('admin.images.colSource'), hideOnMobile: true },
      {
        key: 'status',
        header: t('common.status'),
        render: (r) => (
          <span className={`gs-pill ${BUILD_PILL[r.status] ?? 'bg-surface-2 text-muted'}`}>{r.status}</span>
        ),
      },
      {
        key: 'image_ref',
        header: t('admin.images.colResultImage'),
        render: (r) => <span className="text-muted text-[12px] font-mono">{r.image_ref ?? '—'}</span>,
      },
      {
        key: 'created_at',
        header: t('common.created'),
        align: 'right',
        sortBy: (r) => (r.created_at ? new Date(r.created_at).getTime() : 0),
        render: (r) => <Timestamp value={r.created_at} className="text-muted" />,
      },
    ],
    [t],
  );

  const imageRows = useMemo(
    () => sortRows(images, sortAccessor(imageColumns, table.sort), table.dir),
    [images, imageColumns, table.sort, table.dir],
  );
  const buildRows = useMemo(
    () => sortRows(builds, sortAccessor(buildColumns, table.sort), table.dir),
    [builds, buildColumns, table.sort, table.dir],
  );

  return (
    <div>
      <PageHeader
        title={t('admin.images.title')}
        description={t('admin.images.subtitle')}
        updatedAt={(tab === 'builds' ? buildsQ.dataUpdatedAt : imagesQ.dataUpdatedAt) || null}
        onRefresh={() => (tab === 'builds' ? buildsQ.refetch() : imagesQ.refetch())}
        isFetching={tab === 'builds' ? buildsQ.isFetching : imagesQ.isFetching}
        actions={
          <>
            <Link to="/admin/images/import" className="gs-btn">{t('admin.images.import')}</Link>
            <Link to="/admin/images/build" className="gs-btn gs-btn-primary">{t('admin.images.build')}</Link>
          </>
        }
      />

      <div className="flex gap-2 mb-4" role="tablist" aria-label={t('admin.images.title')}>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'images'}
          className={`gs-btn ${tab === 'images' ? 'gs-btn-primary' : ''}`}
          onClick={() => setTab('images')}
        >
          {t('admin.images.tabCatalogue')}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'builds'}
          className={`gs-btn ${tab === 'builds' ? 'gs-btn-primary' : ''}`}
          onClick={() => setTab('builds')}
        >
          {t('admin.images.tabBuilds')}
        </button>
      </div>

      {tab === 'images' ? (
        <>
          <TableToolbar
            query={q}
            onQueryChange={table.setQuery}
            placeholder={t('admin.images.searchPlaceholder')}
            total={images.length}
            shown={imageRows.length}
            onClear={table.clear}
          >
            <label className="gs-sr-only" htmlFor="gs-image-kind">{t('admin.images.allKinds')}</label>
            <select
              id="gs-image-kind"
              className="gs-input w-auto"
              value={kind}
              onChange={(e) => setKind(e.target.value as typeof kind)}
            >
              <option value="">{t('admin.images.allKinds')}</option>
              <option value="image">image</option>
              <option value="template">template</option>
              <option value="iso">iso</option>
            </select>
          </TableToolbar>
          <div className="gs-card">
            {imagesQ.isLoading ? (
              <TableSkeleton rows={4} columns={5} />
            ) : imageRows.length === 0 ? (
              table.isFiltered
                ? <NoResults query={q} onClear={table.clear} />
                : (
                  <EmptyState
                    icon="▣"
                    title={t('admin.images.emptyImages')}
                    description={t('admin.images.emptyImagesDescription')}
                    action={<Link to="/admin/images/import" className="gs-btn gs-btn-primary">{t('admin.images.import')}</Link>}
                  />
                )
            ) : (
              <Table
                caption={t('admin.images.tabCatalogue')}
                columns={imageColumns}
                rows={imageRows}
                rowKey={(r) => r.id}
                sort={table.sort}
                dir={table.dir}
                onSort={table.toggleSort}
              />
            )}
          </div>
        </>
      ) : (
        <div className="gs-card">
          {buildsQ.isLoading ? (
            <TableSkeleton rows={3} columns={4} />
          ) : buildRows.length === 0 ? (
            <EmptyState
              icon="⚒"
              title={t('admin.images.emptyBuilds')}
              description={t('admin.images.emptyBuildsDescription')}
              action={<Link to="/admin/images/build" className="gs-btn gs-btn-primary">{t('admin.images.build')}</Link>}
            />
          ) : (
            <Table
              caption={t('admin.images.tabBuilds')}
              columns={buildColumns}
              rows={buildRows}
              rowKey={(r) => r.id}
              sort={table.sort}
              dir={table.dir}
              onSort={table.toggleSort}
            />
          )}
          <p className="text-muted text-[11.5px] mt-3">
            {t('admin.images.buildNote')}
          </p>
        </div>
      )}
    </div>
  );
}

// Image import, at /admin/images/import.
export function ImportImagePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [sourceType, setSourceType] = useState<'registry' | 'url'>('registry');
  const [source, setSource] = useState('');
  const [name, setName] = useState('');
  const [kind, setKind] = useState<'image' | 'template' | 'iso'>('image');
  const [cudaVersion, setCudaVersion] = useState('');
  const imp = useImportImage();
  const pushToast = useUiStore((s) => s.pushToast);

  const reset = () => {
    setSource('');
    setName('');
    setKind('image');
    setCudaVersion('');
    setSourceType('registry');
  };

  const submit = () => {
    const body: ImportImageBody = {
      source_type: sourceType,
      source: source.trim(),
      name: name.trim(),
      kind,
      ...(cudaVersion.trim() ? { cuda_version: cudaVersion.trim() } : {}),
    };
    imp.mutate(body, {
      onSuccess: () => { pushToast('success', t('admin.images.importStarted', { name })); reset(); navigate('/admin/images'); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  const valid = source.trim().length > 0 && name.trim().length > 0;
  const guard = useFormGuard(imp.isPending);

  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.images.importTitle')}
        crumbs={[{ label: t('admin.images.title'), to: '/admin/images' }, { label: t('admin.images.importTitle') }]}
        actions={<BackLink to={'/admin/images'} />}
      />
      <form className="gs-card" noValidate {...guard.props} onSubmit={(e) => { e.preventDefault(); if (valid) submit(); }}>
      <div className="grid gap-3">
        <label className="text-[13px] font-semibold">
          {t('admin.images.sourceKind')}
          <select
            className="gs-input mt-1 w-full"
            value={sourceType}
            onChange={(e) => setSourceType(e.target.value as 'registry' | 'url')}
          >
            <option value="registry">registry (registry/repo:tag)</option>
            <option value="url">url (ISO/tarball HTTPS)</option>
          </select>
        </label>
        <label className="text-[13px] font-semibold">
          {t('admin.images.source')}
          <input
            className="gs-input mt-1 w-full font-mono"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            placeholder={sourceType === 'registry' ? 'ghcr.io/acme/pytorch:2.4-cu124' : 'https://…/image.iso'} autoComplete="off" />
        </label>
        <label className="text-[13px] font-semibold">
          {t('common.name')}
          <input className="gs-input mt-1 w-full" value={name} onChange={(e) => setName(e.target.value)} placeholder="PyTorch 2.4 (imported)" autoComplete="off" />
        </label>
        <label className="text-[13px] font-semibold">
          {t('admin.images.kind')}
          <select className="gs-input mt-1 w-full" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
            <option value="image">image</option>
            <option value="template">template</option>
            <option value="iso">iso</option>
          </select>
        </label>
        <label className="text-[13px] font-semibold">
          {t('admin.images.cudaVersion')} <span className="text-muted font-normal">({t('common.optional')})</span>
          <input className="gs-input mt-1 w-full font-mono" value={cudaVersion} onChange={(e) => setCudaVersion(e.target.value)} placeholder={t('admin.images.cudaPlaceholder')} autoComplete="off" />
          <span className="text-muted text-[11px] font-normal mt-1 block">
            {t('admin.images.cudaNote')}
          </span>
        </label>
        <p className="text-muted text-[12px]">{t('admin.images.importNote')}</p>
      </div>
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={valid ? [] : [
          !source.trim() && t('admin.images.source'),
          !name.trim() && t('common.name'),
        ].filter(Boolean) as string[]} />
        <button type="button" className="gs-btn" onClick={() => navigate('/admin/images')}>{t('common.cancel')}</button>
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || imp.isPending}>
          {imp.isPending ? t('admin.images.importing') : t('admin.images.register')}</button>
      </div>
      </form>
    </div>
  );
}

// Image build, at /admin/images/build.
export function BuildImagePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const groups = useProjects().data ?? [];
  const [projectId, setProjectId] = useState('');
  const effGroupId = projectId || groups[0]?.id || '';
  const [name, setName] = useState('');
  const [source, setSource] = useState<'dockerfile' | 'git'>('git');
  const [gitUrl, setGitUrl] = useState('');
  const [gitRef, setGitRef] = useState('main');
  const [dockerfile, setDockerfile] = useState('');
  const build = useCreateBuild();
  const pushToast = useUiStore((s) => s.pushToast);

  const submit = () => {
    const body: CreateBuildBody = {
      group_id: effGroupId,
      name: name.trim(),
      source,
      ...(source === 'git'
        ? { git_url: gitUrl.trim(), git_ref: gitRef.trim() || 'main' }
        : { dockerfile }),
    };
    build.mutate(body, {
      onSuccess: () => { pushToast('success', t('admin.images.buildStarted', { name })); navigate('/admin/images'); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  const valid =
    effGroupId.length > 0 &&
    name.trim().length > 0 &&
    (source === 'git' ? gitUrl.trim().length > 0 : dockerfile.trim().length > 0);
  const guard = useFormGuard(build.isPending);

  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.images.buildTitle')}
        crumbs={[{ label: t('admin.images.title'), to: '/admin/images' }, { label: t('admin.images.buildTitle') }]}
        actions={<BackLink to={'/admin/images'} />}
      />
      <form className="gs-card" noValidate {...guard.props} onSubmit={(e) => { e.preventDefault(); if (valid) submit(); }}>
      <div className="grid gap-3">
        <label className="text-[13px] font-semibold">
          {t('common.group')}
          <select className="gs-input mt-1 w-full" value={effGroupId} onChange={(e) => setProjectId(e.target.value)}>
            {groups.length === 0 && <option value="">{t('admin.images.noGroup')}</option>}
            {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select>
        </label>
        <label className="text-[13px] font-semibold">
          {t('admin.images.imageName')}
          <input className="gs-input mt-1 w-full" value={name} onChange={(e) => setName(e.target.value)} placeholder="vision-train" autoComplete="off" />
        </label>
        <label className="text-[13px] font-semibold">
          {t('admin.images.source')}
          <select className="gs-input mt-1 w-full" value={source} onChange={(e) => setSource(e.target.value as 'dockerfile' | 'git')}>
            <option value="git">git</option>
            <option value="dockerfile">dockerfile</option>
          </select>
        </label>
        {source === 'git' ? (
          <>
            <label className="text-[13px] font-semibold">
              Git URL
              <input className="gs-input mt-1 w-full font-mono" value={gitUrl} onChange={(e) => setGitUrl(e.target.value)} placeholder="https://git.example.com/vision/trainer.git" autoComplete="off" />
            </label>
            <label className="text-[13px] font-semibold">
              Git Ref
              <input className="gs-input mt-1 w-full font-mono" value={gitRef} onChange={(e) => setGitRef(e.target.value)} placeholder="main" autoComplete="off" />
            </label>
          </>
        ) : (
          <label className="text-[13px] font-semibold">
            Dockerfile
            <textarea
              className="gs-input mt-1 w-full font-mono h-40"
              value={dockerfile}
              onChange={(e) => setDockerfile(e.target.value)}
              placeholder={'FROM nvidia/cuda:12.4.0-runtime\nRUN pip install torch'}
            />
          </label>
        )}
        <p className="text-muted text-[12px]">{t('admin.images.buildNote2')}</p>
      </div>
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={valid ? [] : [
          !effGroupId && t('common.group'),
          !name.trim() && t('admin.images.imageName'),
          source === 'git' && !gitUrl.trim() && 'Git URL',
          source === 'dockerfile' && !dockerfile.trim() && 'Dockerfile',
        ].filter(Boolean) as string[]} />
        <button type="button" className="gs-btn" onClick={() => navigate('/admin/images')}>{t('common.cancel')}</button>
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || build.isPending}>
          {build.isPending ? t('admin.images.starting') : t('admin.images.startBuild')}</button>
      </div>
      </form>
    </div>
  );
}
