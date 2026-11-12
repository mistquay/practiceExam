// 路由和导航共用访问规则；未知路径不作为可见入口。
export function canAccess(route, user) {
  return !!route && (route.public || (!!user && (!route.admin || user.role === "admin")));
}
