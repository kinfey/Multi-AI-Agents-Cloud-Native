# Dockerfile for Blog App (main container - Nginx web viewer)

FROM nginx:1.27-alpine

# Copy custom Nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy index page
COPY index.html /usr/share/nginx/html/index.html

# Copy monitor script
COPY monitor.sh /usr/local/bin/monitor.sh
RUN chmod +x /usr/local/bin/monitor.sh

# Create blog directory for shared volume mount point
RUN mkdir -p /usr/share/nginx/html/blog

# Set labels for identification and cleanup
LABEL project="ghcli-blog-agent" \
      description="Blog App - Main Container (Nginx Web Viewer)"

# Expose web port
EXPOSE 80

# Start both Nginx and monitor
CMD sh -c '/usr/local/bin/monitor.sh & nginx -g "daemon off;"'
