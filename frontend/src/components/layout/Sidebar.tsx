import { useEffect, useState } from 'react';
import { useNavigate, useLocation } from 'react-router';
import {
  LayoutDashboard,
  Mail,
  Table2,
  BarChart3,
  Building2,
  ScrollText,
  Users,
  Settings,
  LogOut,
  BookMarked,
  Shuffle,
  Database,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
} from 'lucide-react';
import APCOTEX_LOGO from '../../assets/images/apcotexindustrieslogo.png';
import { BLUE, TEAL, RED, BORDER } from '../../constants/theme';
import { isAdminRole, isSuperAdminRole } from '../../utils/rbac';
import type { OutlookSyncPermission, UserRole } from '../../types';

const SIDEBAR_COLLAPSED_KEY = 'apcotex.sidebar.collapsed';
const WORKSPACE_COLLAPSED_KEY = 'apcotex.sidebar.workspaceCollapsed';

const EXPANDED_WIDTH = 240;
const COLLAPSED_WIDTH = 68;

const mainNavItems = [
  { path: '/',                  label: 'Dashboard',         icon: LayoutDashboard, exact: true },
  { path: '/emails',            label: 'Emails',            icon: Mail, emailsOnly: true },
  { path: '/consolidated-data', label: 'Consolidated Data', icon: Table2           },
  { path: '/visualizations',    label: 'Visualizations & Analytics', icon: BarChart3        },
  { path: '/distributors',      label: 'Distributors',      icon: Building2 },
  { path: '/audit-trail',       label: 'Audit Trail',       icon: ScrollText, adminOnly: true },
  { path: '/user-management',   label: 'Persona Management',  icon: Users, adminOnly: true },
  { path: '/normalization',     label: 'Normalization',     icon: Shuffle, superAdminOnly: true },
  { path: '/master-data',       label: 'Master Data',       icon: Database, superAdminOnly: true },
];

const adminNavItems = [
  {
    path: '/admin/header-dictionary',
    label: 'Header Dictionary',
    icon: BookMarked,
    adminOnly: true,
  },
];

function readBool(key: string, fallback = false): boolean {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback;
    return raw === '1' || raw === 'true';
  } catch {
    return fallback;
  }
}

function writeBool(key: string, value: boolean) {
  try {
    localStorage.setItem(key, value ? '1' : '0');
  } catch {
    // ignore quota / private mode
  }
}

export function Sidebar({
  userName = 'Debabrata C',
  userTitle = 'CMO',
  userRole,
  outlookSyncPermission = 'own',
  onLogout,
  collapsed: collapsedProp,
  onCollapsedChange,
}: {
  userName?: string;
  userTitle?: string;
  userRole?: UserRole | null;
  outlookSyncPermission?: OutlookSyncPermission | null;
  onLogout?: () => void;
  collapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsedInternal, setCollapsedInternal] = useState(() => readBool(SIDEBAR_COLLAPSED_KEY));
  const [workspaceCollapsed, setWorkspaceCollapsed] = useState(() => readBool(WORKSPACE_COLLAPSED_KEY));

  const collapsed = collapsedProp ?? collapsedInternal;
  const setCollapsed = (next: boolean) => {
    writeBool(SIDEBAR_COLLAPSED_KEY, next);
    setCollapsedInternal(next);
    onCollapsedChange?.(next);
  };

  useEffect(() => {
    if (collapsedProp !== undefined) setCollapsedInternal(collapsedProp);
  }, [collapsedProp]);

  const toggleWorkspace = () => {
    setWorkspaceCollapsed(prev => {
      const next = !prev;
      writeBool(WORKSPACE_COLLAPSED_KEY, next);
      return next;
    });
  };

  const filteredNavItems = mainNavItems.filter(item => {
    if ('emailsOnly' in item && item.emailsOnly && outlookSyncPermission === 'none') return false;
    if ('superAdminOnly' in item && item.superAdminOnly && !isSuperAdminRole(userRole)) return false;
    if ('adminOnly' in item && item.adminOnly && !isAdminRole(userRole)) return false;
    return true;
  });

  const filteredAdminItems = adminNavItems.filter(item => {
    if (item.adminOnly && !isAdminRole(userRole)) return false;
    return true;
  });

  const isActive = (item: { path: string; exact?: boolean }) => {
    if (item.exact) return location.pathname === item.path;
    return location.pathname === item.path || location.pathname.startsWith(item.path + '/');
  };

  const renderNavButton = (item: {
    path: string;
    label: string;
    icon: typeof LayoutDashboard;
    exact?: boolean;
  }) => {
    const active = isActive(item);
    const Icon = item.icon;
    return (
      <button
        key={item.path}
        onClick={() => navigate(item.path)}
        title={collapsed ? item.label : undefined}
        aria-label={item.label}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          width: '100%',
          padding: collapsed ? '10px 0' : '8px 20px',
          justifyContent: collapsed ? 'center' : 'flex-start',
          background: active ? 'rgba(31,95,168,0.06)' : 'transparent',
          border: 'none',
          borderLeft: active ? `2px solid ${TEAL}` : '2px solid transparent',
          color: active ? BLUE : '#6B7280',
          cursor: 'pointer',
          textAlign: 'left',
          fontSize: '0.875rem',
          fontWeight: active ? 600 : 400,
          transition: 'background 0.12s, color 0.12s',
        }}
        onMouseEnter={e => {
          if (!active) {
            e.currentTarget.style.background = '#F9FAFB';
            e.currentTarget.style.color = '#374151';
          }
        }}
        onMouseLeave={e => {
          if (!active) {
            e.currentTarget.style.background = 'transparent';
            e.currentTarget.style.color = '#6B7280';
          }
        }}
      >
        <Icon size={17} strokeWidth={active ? 2 : 1.5} style={{ flexShrink: 0 }} />
        {!collapsed && <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.label}</span>}
      </button>
    );
  };

  const width = collapsed ? COLLAPSED_WIDTH : EXPANDED_WIDTH;

  return (
    <div
      style={{
        width,
        minWidth: width,
        background: 'white',
        borderRight: `1px solid ${BORDER}`,
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        flexShrink: 0,
        transition: 'width 0.2s ease, min-width 0.2s ease',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          padding: collapsed ? '10px 8px' : '10px 12px 10px 16px',
          borderBottom: `1px solid ${BORDER}`,
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: collapsed ? 'center' : 'space-between',
          gap: 8,
          minHeight: 76,
        }}
      >
        <img
          src={APCOTEX_LOGO}
          alt="Apcotex"
          style={{
            height: collapsed ? 36 : 60,
            width: 'auto',
            maxWidth: collapsed ? 44 : 160,
            objectFit: 'contain',
            transition: 'height 0.2s ease',
          }}
        />
        {!collapsed && (
          <button
            type="button"
            onClick={() => setCollapsed(true)}
            title="Collapse sidebar"
            aria-label="Collapse sidebar"
            style={{
              border: `1px solid ${BORDER}`,
              background: 'white',
              borderRadius: 6,
              width: 28,
              height: 28,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              color: '#6B7280',
              flexShrink: 0,
            }}
          >
            <ChevronLeft size={16} />
          </button>
        )}
      </div>

      {collapsed && (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '8px 0 4px' }}>
          <button
            type="button"
            onClick={() => setCollapsed(false)}
            title="Expand sidebar"
            aria-label="Expand sidebar"
            style={{
              border: `1px solid ${BORDER}`,
              background: 'white',
              borderRadius: 6,
              width: 28,
              height: 28,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              color: '#6B7280',
            }}
          >
            <ChevronRight size={16} />
          </button>
        </div>
      )}

      {!collapsed && (
        <div style={{ padding: '12px 12px 6px 20px', flexShrink: 0 }}>
          <button
            type="button"
            onClick={toggleWorkspace}
            aria-expanded={!workspaceCollapsed}
            style={{
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              background: 'transparent',
              border: 'none',
              padding: 0,
              cursor: 'pointer',
            }}
          >
            <span
              style={{
                fontSize: '0.6875rem',
                color: '#9CA3AF',
                letterSpacing: '0.07em',
                textTransform: 'uppercase',
                fontWeight: 600,
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
              }}
            >
              Workspace
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: RED }} />
            </span>
            <ChevronDown
              size={14}
              color="#9CA3AF"
              style={{
                transform: workspaceCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)',
                transition: 'transform 0.2s ease',
              }}
            />
          </button>
        </div>
      )}

      <nav style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden' }}>
        {(collapsed || !workspaceCollapsed) && filteredNavItems.map(renderNavButton)}

        {!collapsed && filteredAdminItems.length > 0 && (
          <>
            <div style={{ padding: '16px 20px 6px' }}>
              <span
                style={{
                  fontSize: '0.6875rem',
                  color: '#9CA3AF',
                  letterSpacing: '0.07em',
                  textTransform: 'uppercase',
                  fontWeight: 600,
                }}
              >
                Admin
              </span>
            </div>
            {filteredAdminItems.map(renderNavButton)}
          </>
        )}
        {collapsed && filteredAdminItems.map(renderNavButton)}
      </nav>

      <div style={{ borderTop: `1px solid ${BORDER}`, flexShrink: 0 }}>
        {isAdminRole(userRole) && (
          <button
            onClick={() => navigate('/settings')}
            title={collapsed ? 'Settings' : undefined}
            aria-label="Settings"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              width: '100%',
              padding: collapsed ? '10px 0' : '8px 20px',
              justifyContent: collapsed ? 'center' : 'flex-start',
              background: location.pathname.startsWith('/settings') ? 'rgba(31,95,168,0.06)' : 'transparent',
              border: 'none',
              borderLeft: location.pathname.startsWith('/settings') ? `2px solid ${TEAL}` : '2px solid transparent',
              color: location.pathname.startsWith('/settings') ? BLUE : '#6B7280',
              cursor: 'pointer',
              fontSize: '0.875rem',
              fontWeight: location.pathname.startsWith('/settings') ? 600 : 400,
            }}
          >
            <Settings size={17} strokeWidth={1.5} />
            {!collapsed && 'Settings'}
          </button>
        )}

        <div style={{ borderTop: `1px solid ${BORDER}` }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: collapsed ? '12px 0' : '12px 20px',
              justifyContent: collapsed ? 'center' : 'flex-start',
            }}
          >
            <div
              style={{
                width: 30,
                height: 30,
                borderRadius: '50%',
                background: BLUE,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
              }}
              title={collapsed ? `${userName} · ${userTitle}` : undefined}
            >
              <span style={{ color: 'white', fontSize: '0.6875rem', fontWeight: 700 }}>
                {userName.split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase()}
              </span>
            </div>

            {!collapsed && (
              <>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ color: '#1F2937', fontSize: '0.8125rem', fontWeight: 600, lineHeight: 1.3 }}>
                    {userName}
                  </div>
                  <div
                    style={{
                      color: '#9CA3AF',
                      fontSize: '0.6875rem',
                      lineHeight: 1.3,
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {userTitle}
                  </div>
                </div>

                <button
                  onClick={onLogout}
                  title="Logout"
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: '#6B7280',
                    cursor: 'pointer',
                    padding: 4,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    borderRadius: 4,
                  }}
                >
                  <LogOut size={16} strokeWidth={2} />
                </button>
              </>
            )}
            {collapsed && (
              <button
                onClick={onLogout}
                title="Logout"
                aria-label="Logout"
                style={{
                  position: 'absolute',
                  width: 1,
                  height: 1,
                  overflow: 'hidden',
                  clip: 'rect(0 0 0 0)',
                }}
              />
            )}
          </div>
          {collapsed && (
            <div style={{ display: 'flex', justifyContent: 'center', paddingBottom: 10 }}>
              <button
                onClick={onLogout}
                title="Logout"
                aria-label="Logout"
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: '#6B7280',
                  cursor: 'pointer',
                  padding: 4,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  borderRadius: 4,
                }}
              >
                <LogOut size={16} strokeWidth={2} />
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export { EXPANDED_WIDTH, COLLAPSED_WIDTH, SIDEBAR_COLLAPSED_KEY };
