'use strict';
'require baseclass';
'require ui';

/*
 * 统一 UI 菜单（v2.1.0 起）
 *
 * 本文件替代 vendor 主题的同名文件（build.sh 拷贝主题后覆盖，见 2b-1 步骤）。
 * 与 vendor 版的差异：
 *   - 删除简单/高级双模式：getMenuCategory / switchCategory /
 *     menu-item-basic / menu-item-advanced / localStorage('luci-menu-category') /
 *     renderModeMenu / #modemenu 全部移除，系统只有一种 UI 展示模式。
 *   - 所有顶级菜单常驻侧边栏（fwx 业务页 + LuCI 原生页），按业务顺序排列；
 *     清单外的新节点追加在 logout 之前，保证不遗漏任何功能入口。
 *   - 无标题的匿名节点（admin/fwx 等 API 容器）不进菜单；
 *     fwx_network（luci-app-fwx-network 移除后失去标题）补显示名「Firewall」。
 * 设计系统样式见 design-system.css（构建时由 style_bridge.py 追加到 cascade.css）。
 */

return baseclass.extend({
	menuIcons: {
		'fwx_network': 'fwx_network',
		'fwx_wireless': 'fwx_wireless',
		'fwx_parental_control': 'fwx_parental_control',
		'fwx_advance': 'fwx_advance',
		'fwx_dashboard': 'fwx_dashboard',
		'fwx_internet_record': 'fwx_internet_record',
		'fwx_user': 'fwx_user',
		'fwx_monitor': 'fwx_monitor',
		'fwx_accel': 'fwx_advance',
	},
	defaultIcon: 'default',

	/* 统一侧边栏的业务顺序（docs/ui-unify-plan.html 清单二）。
	 * 不在表内的节点按原注册顺序排在 logout 之前。 */
	MENU_ORDER: [
		'fwx_dashboard',        // 运行概览
		'fwx_user',             // 终端管理
		'fwx_parental_control', // 应用管控（应用过滤 / MAC 过滤 / 上网黑名单）
		'fwx_internet_record',  // 上网审计（审计开关 / 审计白名单）
		'fwx_monitor',          // 数据监控（会话 / 流量 / 上下线记录）
		'fwx_accel',            // 网络加速
		'network',              // LuCI 网络（接口 / 无线 / EQoS / TurboAcc / 诊断）
		'fwx_network',          // 防火墙（fwx 基础防火墙）
		'firewall',             // LuCI 防火墙（区域 / 转发 / 规则）
		'status',               // LuCI 状态（概览 / 日志 / 进程）
		'fwx_advance',          // 系统管理（仪表板设置 / 特征库 / fwx 系统配置）
		'system',               // LuCI 系统（管理 / 软件包 / 启动项 / ttyd）
		'logout',               // 退出登录
	],

	/* 标题覆盖：节点本身无标题（注册它的应用已移除），但页面必须可达。
	 * 'Firewall' 是 luci-i18n-base-zh-cn 已有的词条（防火墙）。 */
	MENU_TITLE_OVERRIDES: {
		'fwx_network': 'Firewall',
	},

	__init__() {
		ui.menu.load().then(L.bind(this.render, this));
		window.addEventListener('resize', L.bind(this.handleResize, this));
	},

	isMobileDevice() {
		const userAgent = navigator.userAgent || navigator.vendor || window.opera;
		const mobileRegex = /android|webos|iphone|ipad|ipod|blackberry|iemobile|opera mini/i;
		const isMobileUA = mobileRegex.test(userAgent);

		const width = window.innerWidth || document.documentElement.clientWidth || document.body.clientWidth;
		const isMobileWidth = width <= 768;

		const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0;

		const isHighDPR = window.devicePixelRatio && window.devicePixelRatio > 1;

		const isMobile = isMobileUA && isMobileWidth;

		const isMobileLandscape = isMobileUA && width > 768 && width <= 1024;

		const result = isMobile || isMobileLandscape;

		return result;
	},

	isWideScreen() {
		return !this.isMobileDevice();
	},

	handleResize() {
		clearTimeout(this.resizeTimer);
		this.resizeTimer = setTimeout(() => {
			const isWide = this.isWideScreen();
			const sidebarNav = document.querySelector('.sidebar-nav');

			if (isWide) {
				if (sidebarNav) {
					sidebarNav.style.display = '';
				}
			} else {
				if (sidebarNav) {
					sidebarNav.style.display = 'none';
				}
			}
		}, 200);
	},

	getMenuIcon(menuName) {
		return this.menuIcons[menuName] || this.defaultIcon;
	},

	generateMenuIconClass(menuName) {
		const iconName = this.getMenuIcon(menuName);
		return `menu-icon-${iconName}`;
	},

	render(tree) {
		let node = tree;
		let url = '';
		this.renderCategoryNav();
		this.renderBreadcrumb(tree);

		let currentLevel = 0;
		if (L.env.dispatchpath.length >= 3) {
			for (var i = 0; i < 3 && node; i++) {
				node = node.children[L.env.dispatchpath[i]];
				url = url + (url ? '/' : '') + L.env.dispatchpath[i];
			}

			currentLevel = 1;

			if (node)
				this.renderTabMenu(node, url, currentLevel);
		}

		const isWide = this.isWideScreen();
		const menuSelector = isWide ? '#topmenu' : '#topmenu-mobile';
		const menuContainer = document.querySelector(menuSelector);
		const hasRenderedMenu = !!(menuContainer && menuContainer.children.length > 0);
		if (!hasRenderedMenu) {
			if (isWide) {
				this.renderMainMenu(node, url, currentLevel);
			} else {
				this.renderMobileMenu(node, url, currentLevel);
			}
		}

		setTimeout(() => {
			this.handleResize();
		}, 100);
	},

	renderCategoryNav() {
		const isWide = this.isWideScreen();

		if (isWide) {
			const titleText = 'FanchmWrt';
			const brandElement = E('a', { 'class': 'top-navbar-brand', 'href': L.url('admin', 'fwx_dashboard') }, [
				E('span', { 'class': 'top-navbar-title' }, [titleText])
			]);

			const userMenu = E('div', { 'class': 'top-user-menu top-profile-menu' }, [
				E('button', {
					'class': 'top-user-button',
					'type': 'button',
					'aria-label': _('User menu')
				}, [
					E('span', { 'class': 'top-user-icon', 'aria-hidden': 'true' })
				]),
				E('ul', { 'class': 'top-user-dropdown top-profile-dropdown' }, [
					E('li', { 'class': 'logout-item' }, [
						E('a', { 'href': '#' }, [_('Logout')])
					])
				])
			]);

			const closeTopMenus = () => {
				userMenu.classList.remove('open');
			};

			const userBtn = userMenu.querySelector('.top-user-button');
			userBtn.addEventListener('click', function(ev) {
				ev.preventDefault();
				ev.stopPropagation();
				const shouldOpen = !userMenu.classList.contains('open');
				closeTopMenus();
				if (shouldOpen) {
					userMenu.classList.add('open');
				}
			});

			const logoutLink = userMenu.querySelector('.logout-item a');
			logoutLink.addEventListener('click', function(ev) {
				ev.preventDefault();
				ev.stopPropagation();
				closeTopMenus();
				window.dispatchEvent(new CustomEvent('logout', {
					detail: { source: 'top-profile-menu' }
				}));
				window.location.href = L.url('admin', 'logout');
			});

			document.addEventListener('click', function(ev) {
				if (!userMenu.contains(ev.target)) {
					closeTopMenus();
				}
			});

			document.addEventListener('keydown', function(ev) {
				if (ev.key === 'Escape') {
					closeTopMenus();
				}
			});

			const topNavbarContent = E('div', { 'class': 'top-navbar-content' }, [
				brandElement,
				E('div', { 'class': 'top-navbar-right' }, [userMenu])
			]);
			const topNavbar = E('div', { 'class': 'top-navbar' }, [topNavbarContent]);

		const body = document.body;
			const existingNavbar = document.querySelector('.top-navbar');
			if (existingNavbar) {
				existingNavbar.remove();
			}
		if (body.firstChild) {
			body.insertBefore(topNavbar, body.firstChild);
		} else {
			body.appendChild(topNavbar);
		}
		}
	},

	renderBreadcrumb(tree) {
		if (L.env.dispatchpath && L.env.dispatchpath.length >= 2 &&
			L.env.dispatchpath[0] === 'admin' && L.env.dispatchpath[1] === 'dashboard') {
			return;
		}

		if (!L.env.dispatchpath || L.env.dispatchpath.length === 0) {
			return;
		}

		const breadcrumbItems = [];
		let currentNode = tree;
		let currentPath = '';

		for (let i = 0; i < L.env.dispatchpath.length && currentNode; i++) {
			const pathSegment = L.env.dispatchpath[i];
			currentPath += (currentPath ? '/' : '') + pathSegment;

			if (currentNode.children && currentNode.children[pathSegment]) {
				currentNode = currentNode.children[pathSegment];

				if (i === 0 && pathSegment === 'admin') {
					const homeItem = E('li', { 'class': 'home-item' }, [
						E('a', { 'href': '/cgi-bin/luci/admin/fwx_dashboard', 'title': _('Home') }, [
							E('span', { 'class': 'home-icon' }, ['🏠'])
						])
					]);
					breadcrumbItems.push(homeItem);
				} else {
					const displayName = this.menuDisplayName(currentNode);
					if (displayName === null) {
						continue;
					}
					const breadcrumbItem = E('li', {}, [
						E('span', {}, [displayName])
					]);

					breadcrumbItems.push(breadcrumbItem);
				}
			}
		}

		if (breadcrumbItems.length === 0) {
			return;
		}

		const breadcrumbContainer = E('div', { 'class': 'breadcrumb-container' }, [
			E('ol', { 'class': 'breadcrumb' }, breadcrumbItems)
		]);

		const mainContent = document.querySelector('#maincontent') || document.querySelector('.main');
		if (mainContent) {
			const existingBreadcrumb = mainContent.querySelector('.breadcrumb-container');
			if (existingBreadcrumb) {
				existingBreadcrumb.remove();
			}

			mainContent.insertBefore(breadcrumbContainer, mainContent.firstChild);
		}
	},

	/* 节点显示名：标题覆盖优先；无标题且无覆盖返回 null（该节点不进菜单）。 */
	menuDisplayName(node) {
		const override = node && node.name ? this.MENU_TITLE_OVERRIDES[node.name] : undefined;
		if (override !== undefined) {
			return _(override);
		}
		if (node && node.title) {
			return _(node.title);
		}
		return null;
	},

	/* 顶级菜单排序：MENU_ORDER 在前，其余按原顺序跟在后面。 */
	sortTopLevel(children) {
		const order = this.MENU_ORDER;
		const last = order.length;
		return children.slice().sort((a, b) => {
			const ra = order.indexOf(a.name);
			const rb = order.indexOf(b.name);
			return (ra === -1 ? last : ra) - (rb === -1 ? last : rb);
		});
	},

	isAlreadyRendered(tree, level, parentName) {
		const treeName = tree.name;

		if (level === 1 && parentName) {
			const existingSubmenus = document.querySelectorAll('#topmenu .dropdown-menu a');
			for (let link of existingSubmenus) {
				const href = link.getAttribute('href');
				if (href) {
					const pathMatch = href.match(/\/admin\/([^\/\?#]+)\/([^\/\?#]+)/);
					if (pathMatch) {
						const existingParentMenu = pathMatch[1];
						const existingChildMenu = pathMatch[2];

						if (existingParentMenu === parentName && existingChildMenu === treeName) {
							return true;
						}
					}
				}
			}
		}

		if (L.env.dispatchpath && L.env.dispatchpath.length > 0) {
			let treeIndex = -1;
			for (let i = 0; i < L.env.dispatchpath.length; i++) {
				if (L.env.dispatchpath[i] === treeName) {
					treeIndex = i;
					break;
				}
			}

			if (treeIndex > 2) {
				return true;
			}
		}

		return false;
	},

	renderTabMenu(tree, url, level) {
		level = level || 0;

		const container = document.querySelector('#tabmenu');
		const ul = E('ul', { 'class': 'tabs' });
		const children = ui.menu.getChildren(tree);
		let activeNode = null;

		children.forEach(child => {
			const isActive = (L.env.dispatchpath[3 + level] == child.name);
			const activeClass = isActive ? ' active' : '';
			const className = 'tabmenu-item-%s %s'.format(child.name, activeClass);

			ul.appendChild(E('li', { 'class': className }, [
				E('a', { 'href': L.url(url, child.name) }, [ _(child.title) ] )]));

			if (isActive)
				activeNode = child;
		});

		if (ul.children.length == 0)
			return E([]);

		container.appendChild(ul);
		container.style.display = '';

		if (activeNode)
			this.renderTabMenu(activeNode, url + '/' + activeNode.name, level + 1);

		return ul;
	},

	renderMainMenu(tree, url, level) {
		level = level || 0;

		const isWide = this.isWideScreen();
		let ul;

		if (level === 0) {
			ul = document.querySelector('#topmenu');
		} else {
			ul = E('ul', { 'class': 'dropdown-menu' });
		}

		let children = ui.menu.getChildren(tree);

		if (children.length == 0 || level > 1)
			return E([]);

		const parentName = (level === 1) ? tree.name : null;
		if (this.isAlreadyRendered(tree, level, parentName)) {
			return E([]);
		}

		/* 统一 UI：顶级菜单按业务顺序排列，全部常驻；
		 * 无标题且无覆盖的节点（如 admin/fwx 这类 API 容器）不进菜单。 */
		if (level === 0) {
			children = this.sortTopLevel(children);
		}
		children = children.filter(child => {
			return level > 0 || this.menuDisplayName(child) !== null;
		});

		children.forEach(child => {

			const submenu = this.renderMainMenu(child, url + '/' + child.name, level + 1);
			const subclass = (level === 0 && submenu.firstElementChild) ? 'dropdown' : '';
			const linkclass = (level === 0 && submenu.firstElementChild) ? 'menu' : '';
			const linkurl = submenu.firstElementChild ? '#' : L.url(url, child.name);

			const iconClass = level === 0 ? this.generateMenuIconClass(child.name) : '';
			const finalLinkClass = linkclass + (iconClass ? ' ' + iconClass : '');

			const li = E('li', { 'class': subclass }, [
				E('a', { 'class': finalLinkClass, 'href': linkurl }, [ this.menuDisplayName(child) ]),
				submenu
			]);

			if (level === 0 && submenu.firstElementChild) {
				const isActive = ((L.env.dispatchpath[1] == child.name) && (L.env.dispatchpath[0] == tree.name));
				if (isActive) {
					li.classList.add('active', 'open');
				}
			}

			if (level === 1) {
				const isActive = ((L.env.dispatchpath[2] == child.name) && (L.env.dispatchpath[1] == tree.name));
				if (isActive) {
					li.classList.add('active');
				}
			}

			if (level === 0 && submenu.firstElementChild) {
				const menuLink = li.querySelector('a.menu');
				menuLink.addEventListener('click', function(e) {
					e.preventDefault();
					e.stopPropagation();
					const dropdown = li;
					const isOpen = dropdown.classList.contains('open');

					document.querySelectorAll('#topmenu .dropdown.open').forEach(item => {
						if (item !== dropdown) {
							item.classList.remove('open');
						}
					});

					if (isOpen) {
						dropdown.classList.remove('open');
					} else {
						dropdown.classList.add('open');
					}
				});
			}

			if (level === 1) {
				const submenuLink = li.querySelector('a');
				if (submenuLink) {
					submenuLink.addEventListener('click', function(e) {
						e.stopPropagation();

						if (!this.isWideScreen()) {
							this.hideMobileMenu();
						}
					}.bind(this));
				}
			}

			if (level === 0 && !submenu.firstElementChild) {
				const menuLink = li.querySelector('a');
				if (menuLink) {
					menuLink.addEventListener('click', function(e) {

						if (!this.isWideScreen()) {
							this.hideMobileMenu();
						}
					}.bind(this));
				}
			}

			ul.appendChild(li);
		});

		ul.style.display = '';

		return ul;
	},

	renderMobileMenu(tree, url, level) {
		level = level || 0;

		let ul;

		if (level === 0) {
			ul = document.querySelector('#topmenu-mobile');
			if (!ul) {
				console.error('topmenu-mobile not found in header!');
				return E([]);
			}
		} else {
			ul = E('ul', { 'class': 'dropdown-menu' });
		}

		let children = ui.menu.getChildren(tree);

		if (children.length == 0 || level > 1)
			return E([]);

		const parentName = (level === 1) ? tree.name : null;
		if (this.isAlreadyRendered(tree, level, parentName)) {
			return E([]);
		}

		if (level === 0) {
			children = this.sortTopLevel(children);
		}
		children = children.filter(child => {
			return level > 0 || this.menuDisplayName(child) !== null;
		});

		children.forEach(child => {

			const submenu = this.renderMobileMenu(child, url + '/' + child.name, level + 1);
			const subclass = (level === 0 && submenu.firstElementChild) ? 'dropdown' : '';
			const linkclass = (level === 0 && submenu.firstElementChild) ? 'menu' : '';
			const linkurl = submenu.firstElementChild ? '#' : L.url(url, child.name);

			const fullClass = subclass;

			const iconClass = level === 0 ? this.generateMenuIconClass(child.name) : '';
			const finalLinkClass = linkclass + (iconClass ? ' ' + iconClass : '');

			const li = E('li', { 'class': fullClass }, [
				E('a', { 'class': finalLinkClass, 'href': linkurl }, [ this.menuDisplayName(child) ]),
				submenu
			]);

			if (level === 0 && submenu.firstElementChild) {
				const isActive = ((L.env.dispatchpath[1] == child.name) && (L.env.dispatchpath[0] == tree.name));
				if (isActive) {
					li.classList.add('active', 'open');
				}
			}

			if (level === 1) {
				const isActive = ((L.env.dispatchpath[2] == child.name) && (L.env.dispatchpath[1] == tree.name));
				if (isActive) {
					li.classList.add('active');
				}
			}

			if (level === 0 && submenu.firstElementChild) {
				const menuLink = li.querySelector('a.menu');
				menuLink.addEventListener('click', function(e) {
					e.preventDefault();
					e.stopPropagation();
					const dropdown = li;
					const isOpen = dropdown.classList.contains('open');

					document.querySelectorAll('#topmenu-mobile .dropdown.open').forEach(item => {
						if (item !== dropdown) {
							item.classList.remove('open');
						}
					});

					if (isOpen) {
						dropdown.classList.remove('open');
					} else {
						dropdown.classList.add('open');
					}
				});
			}

			ul.appendChild(li);
		});

		if (ul && level === 0) {
			ul.style.display = '';
		}

		return ul;
	}
});
